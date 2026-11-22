"""Chat with Claude (Haiku) - only for the allowed user, in DMs or when the bot is mentioned."""

from ..i18n import N_, tr
import asyncio
import datetime as dt
import logging
import time
from collections import defaultdict, deque

import anthropic
import discord
from discord import app_commands
from discord.ext import commands

from .. import secrets_store
from . import ai_providers, claude_actions

log = logging.getLogger("botd.claude")

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
MODEL = MODELS["haiku"]
MAX_TOKENS = 4000
# list prices per million tokens (input, output) - for the cost estimate in the app only
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
          **ai_providers.PRICES}
# Sonnet/Opus 5.5 can decline some requests for safety reasons; the API can then retry on another model
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DISCORD_LIMIT = 1900
LANGUAGE_RULE = ("\n\nAlways reply in the same language as the user's latest message "
                 "(English → English, Hebrew → Hebrew, Russian → Russian, Arabic → Arabic), "
                 "even if these instructions are written in another language.")


def should_respond(*, is_dm: bool, author_id: str, channel_id: str, parent_id: str | None,
                   mentioned: bool, reply_to_bot: bool, allowed_user: str, allowed_channel: str,
                   reply_without_mention: bool) -> bool:
    """Pure trigger rule. Only the allowed user is ever answered."""
    if not allowed_user or author_id != allowed_user:
        return False
    if is_dm:
        return True
    if allowed_channel and allowed_channel not in (channel_id, parent_id):
        return False
    return mentioned or reply_to_bot or (reply_without_mention and bool(allowed_channel))


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split a long reply into Discord-sized chunks, preferring line breaks."""
    text = text.strip()
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks or ["…"]


YES_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "do it", "כן", "אשר", "אישור", "בטח", "да", "ок", "نعم", "أجل"}  # i18n: ignore
NO_WORDS = {"no", "n", "nope", "cancel", "stop", "לא", "ביטול", "בטל", "нет", "отмена", "لا", "إلغاء"}  # i18n: ignore
MAX_TOOL_ROUNDS = 3


def yes_no(text: str) -> bool | None:
    word = text.strip().lower().strip(".!?, ")
    if word in YES_WORDS:
        return True
    if word in NO_WORDS:
        return False
    return None


def usage_key(day: dt.date | None = None) -> str:
    return f"claude_usage:{(day or dt.date.today()).isoformat()}"


class ClaudeChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.history: dict[int, deque] = {}
        self._last: dict[int, float] = defaultdict(float)
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_key: str | None = None
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.pending: dict[int, discord.ui.View] = {}  # channel id -> confirmation waiting for an answer

    async def cog_unload(self) -> None:
        if self._client is not None:
            await self._client.close()

    def _anthropic(self) -> anthropic.AsyncAnthropic | None:
        key = secrets_store.get("anthropic_key")
        if not key:
            return None
        if self._client is None or key != self._client_key:
            self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=60)
            self._client_key = key
        return self._client

    def _config_for(self, message: discord.Message) -> dict | None:
        if message.guild is not None:
            return self.bot.cfg(message.guild, "claude_chat")
        # DMs use the settings of the first server where the module is on
        for guild in self.bot.guilds:
            cfg = self.bot.cfg(guild, "claude_chat")
            if cfg:
                return cfg
        return None

    def _clean_text(self, message: discord.Message) -> str:
        text = message.content
        if self.bot.user is not None:
            for form in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
                text = text.replace(form, "")
        return text.strip()

    def _record_usage(self, usage, model: str = MODEL, count: bool = True) -> dict:
        key = usage_key()
        data = self.bot.store.get_kv(key, {"messages": 0, "input_tokens": 0, "output_tokens": 0})
        tin, tout = int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
        data["messages"] += 1 if count else 0
        data["input_tokens"] += tin
        data["output_tokens"] += tout
        per = data.setdefault("by_model", {}).setdefault(model, [0, 0])
        per[0] += tin
        per[1] += tout
        self.bot.store.set_kv(key, data)
        return data

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or self.bot.user is None:
            return
        settings = self.bot.store.get_settings()
        reply_to_bot = bool(
            message.reference and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        )
        cfg = self._config_for(message)
        if cfg is None:
            return
        view = self.pending.get(message.channel.id)
        if view is not None and str(message.author.id) == settings["allowed_user_id"]:
            answer = yes_no(message.content)
            if answer is not None:  # a typed "yes"/"no" answers the waiting confirmation
                view.value = answer
                view.stop()
                return
        if not should_respond(
            is_dm=message.guild is None,
            author_id=str(message.author.id),
            channel_id=str(message.channel.id),
            parent_id=str(getattr(message.channel, "parent_id", "") or "") or None,
            mentioned=self.bot.user in message.mentions,
            reply_to_bot=reply_to_bot,
            allowed_user=settings["allowed_user_id"],
            allowed_channel=settings["allowed_channel_id"],
            reply_without_mention=cfg["reply_without_mention"],
        ):
            return
        text = self._clean_text(message)
        if not text:
            return
        await self._answer(message, text, cfg)

    async def _answer(self, message: discord.Message, text: str, cfg: dict) -> None:
        channel_id = message.channel.id
        now = time.monotonic()
        if now - self._last[message.author.id] < cfg["cooldown_seconds"]:
            return
        self._last[message.author.id] = now

        cap = cfg["daily_limit"]
        used = self.bot.store.get_kv(usage_key(), {}).get("messages", 0)
        if cap and used >= cap:
            await message.reply(tr("הגעת למגבלה היומית ({n} תשובות). אפשר לשנות אותה באפליקציה.", cfg.get("_lang", "he"), n=cap),
                                mention_author=False)
            return

        provider = cfg.get("provider") or "claude"
        if provider not in ai_providers.PROVIDERS:
            provider = "claude"
        name = ai_providers.PROVIDERS[provider]["name"]
        lang = cfg.get("_lang", "he")
        if provider == "claude":
            client, key = self._anthropic(), None
            missing = client is None
        else:
            client, key = None, secrets_store.get(ai_providers.PROVIDERS[provider]["secret"])
            missing = not key
        if missing:
            await message.reply(tr("עוד לא הוגדר מפתח API של {name} באפליקציה.", lang, name=name), mention_author=False)
            return

        async with self._locks[channel_id]:
            hist = self.history.setdefault(channel_id, deque())
            max_turns = cfg["history_messages"]
            messages = list(hist)[-max_turns * 2:] if max_turns else []
            messages.append({"role": "user", "content": text[:8000]})
            try:
                async with message.channel.typing():
                    if provider == "claude":
                        model = str(cfg.get("custom_model") or "").strip() or MODELS.get(cfg.get("model", "haiku"), MODEL)
                    else:
                        model = ai_providers.model_for(provider, cfg)
                    system = (cfg["system_prompt"] or "You are a helpful assistant.") + LANGUAGE_RULE
                    in_guild = message.guild is not None and isinstance(message.author, discord.Member)
                    tools = claude_actions.available_tools(self.bot, message.guild, bool(cfg.get("allow_actions"))) if in_guild else []
                    if tools:
                        system += "\n\n" + claude_actions.server_context(message.guild, [t["name"] for t in tools])
                    request = dict(model=model, max_tokens=MAX_TOKENS, system=system,
                                   output_config={"effort": cfg["effort"]}, messages=messages)
                    if tools:
                        request["tools"] = tools
                    for _round in range(MAX_TOOL_ROUNDS):
                        if provider != "claude":
                            response = await ai_providers.chat(provider, key, model, request["system"], request["messages"],
                                                               request.get("tools", []), MAX_TOKENS)
                        elif model in (MODELS["sonnet"], MODELS["opus"]):
                            response = await client.beta.messages.create(**request, betas=[FALLBACK_BETA], fallbacks="default")
                        else:
                            response = await client.messages.create(**request)
                        calls = [b for b in response.content if b.type == "tool_use"]
                        if not calls or any(c.name not in claude_actions.READ_NAMES for c in calls):
                            break
                        # read-only lookups: answer them and let Claude continue
                        self._record_usage(response.usage, getattr(response, "model", None) or model, count=False)
                        results = [{"type": "tool_result", "tool_use_id": c.id,
                                    "content": claude_actions.run_read_tool(self.bot, message.guild, c.name, dict(c.input))}
                                   for c in calls]
                        request["messages"] = request["messages"] + [{"role": "assistant", "content": response.content},
                                                                     {"role": "user", "content": results}]
            except anthropic.AuthenticationError:
                log.error(tr("Claude: מפתח Anthropic לא תקין"))
                await message.reply(tr("מפתח Anthropic לא תקין. עדכן אותו באפליקציה.", cfg.get("_lang", "he")), mention_author=False)
                return
            except anthropic.PermissionDeniedError:
                await message.reply(tr("למפתח Anthropic אין הרשאה למודל הזה.", cfg.get("_lang", "he")), mention_author=False)
                return
            except anthropic.RateLimitError:
                await message.reply(tr("יש עומס כרגע. נסה שוב בעוד דקה.", cfg.get("_lang", "he")), mention_author=False)
                return
            except anthropic.APIStatusError as exc:
                log.error(tr("Claude החזיר שגיאה %s (request id %s)"), exc.status_code, getattr(exc, "request_id", "?"))
                await message.reply(tr("Claude לא זמין כרגע. נסה שוב מאוחר יותר.", cfg.get("_lang", "he")), mention_author=False)
                return
            except anthropic.APIConnectionError:
                await message.reply(tr("אין חיבור ל-Anthropic. בדוק את האינטרנט.", cfg.get("_lang", "he")), mention_author=False)
                return
            except ai_providers.ProviderError as exc:
                log.error(tr("%s החזיר שגיאה: %s"), name, exc)
                text_by_kind = {
                    "auth": tr("מפתח ה-API של {name} לא תקין. עדכן אותו באפליקציה.", lang, name=name),
                    "permission": tr("למפתח של {name} אין הרשאה למודל הזה.", lang, name=name),
                    "rate": tr("יש עומס כרגע. נסה שוב בעוד דקה.", lang),
                    "connection": tr("אין חיבור ל-{name}. בדוק את האינטרנט.", lang, name=name),
                }
                await message.reply(text_by_kind.get(exc.kind, tr("{name} לא זמין כרגע. נסה שוב מאוחר יותר.", lang, name=name)),
                                    mention_author=False)
                return

            tool_calls = [b for b in response.content if b.type == "tool_use" and b.name not in claude_actions.READ_NAMES]
            if response.stop_reason == "refusal":
                reply = tr("{name} לא יכול לענות על זה.", lang, name=name)
            elif tool_calls:
                said = "\n".join(b.text for b in response.content if b.type == "text").strip()
                reply = await self._run_actions(message, tool_calls, said, lang, name)
                if max_turns:
                    hist.append({"role": "user", "content": text[:8000]})
                    hist.append({"role": "assistant", "content": (reply or said or "[actions]")[:4000]})
                    while len(hist) > max_turns * 2:
                        hist.popleft()
            else:
                reply = "\n".join(b.text for b in response.content if b.type == "text").strip() or "…"
                if response.stop_reason == "max_tokens":
                    reply += "\n" + tr("*(התשובה נקטעה)*", cfg.get("_lang", "he"))
                if max_turns:
                    hist.append({"role": "user", "content": text[:8000]})
                    hist.append({"role": "assistant", "content": reply})
                    while len(hist) > max_turns * 2:
                        hist.popleft()

        totals = self._record_usage(response.usage, getattr(response, "model", None) or MODEL)
        if reply is None:  # actions already answered in their own message
            return
        log.info(tr("%s ענה (%d טוקנים נכנסים, %d יוצאים). היום: %d תשובות"), name,
                 response.usage.input_tokens, response.usage.output_tokens, totals["messages"])
        chunks = split_message(reply)
        none = discord.AllowedMentions.none()
        try:
            await message.reply(chunks[0], mention_author=False, allowed_mentions=none)
            for chunk in chunks[1:]:
                await message.channel.send(chunk, allowed_mentions=none)
        except discord.HTTPException as exc:
            log.warning(tr("שליחת תשובת Claude נכשלה: %s"), exc.text or exc.status)

    async def _run_actions(self, message: discord.Message, calls, said: str, lang: str, name: str = "Claude") -> str | None:
        """Show Claude's planned server actions, wait for the allowed user to confirm, then run them."""
        from .moderation import ConfirmView

        plans = [claude_actions.plan(message.guild, message.author, c.name, dict(c.input), lang, self.bot, message.channel)
                 for c in calls[:10]]
        ok_plans = [p for p in plans if not p.error]
        lines = [("• " + p.line) if not p.error else f"• ⚠️ {p.line}: {p.error}" for p in plans]
        head = (said + "\n\n" if said else "") + tr("**{name} רוצה לבצע:**", lang, name=name) + "\n" + "\n".join(lines)
        if not ok_plans:
            return head + "\n" + tr("אין פעולה שאפשר לבצע.", lang)
        view = ConfirmView(message.author.id, lang)
        view.timeout = 60
        prompt = await message.reply(head[:1850] + "\n" + tr("לאשר? לחץ על הכפתור או כתוב כן / לא (60 שניות)", lang), view=view,
                                     mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        self.pending[message.channel.id] = view
        try:
            await view.wait()
        finally:
            if self.pending.get(message.channel.id) is view:
                del self.pending[message.channel.id]
        if not view.value:
            text = head[:1800] + "\n" + (tr("בוטל. לא בוצע דבר.", lang) if view.value is False else tr("⌛ פג תוקף. לא בוצע דבר.", lang))
            if view.interaction is not None:
                await view.interaction.response.edit_message(content=text, view=None)
            else:
                await prompt.edit(content=text, view=None)
            return None
        if view.interaction is not None:
            await view.interaction.response.edit_message(content=head[:1800] + "\n" + tr("מבצע…", lang), view=None)
        else:
            await prompt.edit(content=head[:1800] + "\n" + tr("מבצע…", lang), view=None)
        results = [await claude_actions.execute(p, message.author, lang) for p in ok_plans]
        final = (said + "\n\n" if said else "") + "\n".join(results)
        await prompt.edit(content=final[:2000], view=None)
        return None

    @app_commands.command(name="claude_reset", description=N_("מחיקת הזיכרון של השיחה עם Claude בערוץ הזה"))
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def claude_reset(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.bot.store.get_settings()["allowed_user_id"]:
            await interaction.response.send_message(tr("רק המשתמש המורשה יכול להשתמש בפקודה הזו.", self.bot.lang(interaction.guild)), ephemeral=True)
            return
        self.history.pop(interaction.channel_id, None)
        await interaction.response.send_message(tr("🧹 הזיכרון של השיחה בערוץ הזה נמחק.", self.bot.lang(interaction.guild)), ephemeral=True)


def usage_summary(store, days: int = 7) -> dict:
    today = dt.date.today()
    rows = []
    for i in range(days):
        day = today - dt.timedelta(days=i)
        data = store.get_kv(usage_key(day), {"messages": 0, "input_tokens": 0, "output_tokens": 0})
        by_model = data.get("by_model") or {MODEL: [data["input_tokens"], data["output_tokens"]]}
        cost = sum(t_in / 1e6 * PRICES.get(m, PRICES[MODEL])[0] + t_out / 1e6 * PRICES.get(m, PRICES[MODEL])[1]
                   for m, (t_in, t_out) in by_model.items())
        rows.append({"date": day.isoformat(), **data, "est_cost_usd": round(cost, 4)})
    return {"model": MODEL, "days": rows}

