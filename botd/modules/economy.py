"""Economy: custom currency, /daily with streaks, mini-games, a shop, and boosts. Virtual coins only."""

from ..i18n import N_, tr
import datetime as dt
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from .common import can_manage_role, themed

log = logging.getLogger("botd.economy")

DAY = 86400
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
RPS = {"rock": N_("🪨 אבן"), "paper": N_("📄 נייר"), "scissors": N_("✂️ מספריים")}
# what players may type for /roulette, in every supported language
ROULETTE_ALIASES = {"אדום": "red", "שחור": "black", "ירוק": "green",  # i18n: ignore
                    "красный": "red", "чёрный": "black", "черный": "black", "зелёный": "green", "зеленый": "green",
                    "أحمر": "red", "أسود": "black", "أخضر": "green"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


# ---- pure rules (unit-tested) ---------------------------------------------------
def daily_state(last: float, streak: int, now: float) -> tuple[bool, int, float]:
    """(can_claim, new_streak, seconds_until_next). Streak continues if claimed within 48h of the last claim."""
    if last and now - last < DAY:
        return False, streak, DAY - (now - last)
    new_streak = streak + 1 if last and now - last < 2 * DAY else 1
    return True, new_streak, 0


def daily_amount(streak: int, base: int, bonus: int, max_days: int) -> int:
    return base + bonus * min(max(streak - 1, 0), max_days)


def boost_percent(boosts: list[dict], role_ids: set[str], target: str, today: dt.date) -> int:
    """Sum of active boosts for this target ('daily', 'winnings' or 'losses')."""
    total = 0
    for b in boosts:
        if b["target"] != target or (b["role_id"] and b["role_id"] not in role_ids):
            continue
        if b["until"] and dt.date.fromisoformat(b["until"]) < today:
            continue
        total += b["percent"]
    return total


def apply_boost(amount: int, percent: int, target: str) -> int:
    """Boosts add to rewards/winnings and reduce losses (never below zero)."""
    if target == "losses":
        return max(0, round(amount * (100 - min(percent, 100)) / 100))
    return round(amount * (100 + percent) / 100)


def roulette_profit(choice: str, number: int, bet: int) -> int:
    """Net profit (negative = loss). choice: red / black / green / a number 0-36."""
    if choice == "red":
        return bet if number in RED else -bet
    if choice == "black":
        return bet if number and number not in RED else -bet
    if choice == "green":
        return bet * 35 if number == 0 else -bet
    return bet * 35 if choice.isdigit() and int(choice) == number else -bet


def rps_outcome(player: str, bot: str) -> int:
    """1 win, 0 tie, -1 loss."""
    return 0 if player == bot else (1 if BEATS[player] == bot else -1)


def fmt_time(seconds: float, lang: str = "he") -> str:
    h, rem = divmod(int(seconds), 3600)
    return tr("{h} שעות ו-{m} דקות", lang, h=h, m=rem // 60) if h else tr("{n} דקות", lang, n=rem // 60)


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---- helpers ----------------------------------------------------------------
    def _cfg(self, interaction: discord.Interaction) -> dict | None:
        return self.bot.cfg(interaction.guild, "economy")

    @staticmethod
    def money(cfg: dict, amount: int) -> str:
        return f"{cfg['currency_icon']} **{amount:,}** {cfg['currency_name']}"

    @staticmethod
    def _ids(interaction: discord.Interaction) -> tuple[str, str, str]:
        return str(interaction.guild_id), str(interaction.user.id), str(interaction.user)

    @staticmethod
    def _roles(member) -> set[str]:
        return {str(r.id) for r in getattr(member, "roles", [])}

    async def _off(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            tr("מערכת הכלכלה כבויה בשרת הזה. אפשר להפעיל אותה באפליקציה.", self.bot.lang(interaction.guild)), ephemeral=True)

    async def _bet_ok(self, interaction: discord.Interaction, cfg: dict, game: str, bet: int) -> bool:
        if not cfg[f"{game}_on"]:
            await interaction.response.send_message(tr("המשחק הזה כבוי בשרת.", cfg.get("_lang", "he")), ephemeral=True)
            return False
        if not cfg["min_bet"] <= bet <= cfg["max_bet"]:
            await interaction.response.send_message(
                tr("ההימור צריך להיות בין {a} ל-{b}.", cfg.get("_lang", "he"), a=f"{cfg['min_bet']:,}", b=f"{cfg['max_bet']:,}"), ephemeral=True)
            return False
        gid, uid, name = self._ids(interaction)
        if self.bot.store.wallet(gid, uid, name, cfg["start_balance"])["balance"] < bet:
            await interaction.response.send_message(tr("אין לך מספיק {currency}.", cfg.get("_lang", "he"), currency=cfg["currency_name"]), ephemeral=True)
            return False
        return True

    def _settle(self, interaction: discord.Interaction, cfg: dict, profit: int) -> tuple[int, int]:
        """Applies boosts to a game result and updates the balance. Returns (final_profit, balance)."""
        gid, uid, name = self._ids(interaction)
        today = dt.date.today()
        roles = self._roles(interaction.user)
        if profit > 0:
            profit = apply_boost(profit, boost_percent(cfg["boosts"], roles, "winnings", today), "winnings")
        elif profit < 0:
            profit = -apply_boost(-profit, boost_percent(cfg["boosts"], roles, "losses", today), "losses")
        balance = self.bot.store.add_coins(gid, uid, name, profit, cfg["start_balance"], allow_negative=False)
        if balance is None:  # balance dropped meanwhile; take what is there
            balance = self.bot.store.wallet(gid, uid)["balance"]
            self.bot.store.add_coins(gid, uid, name, -balance)
            profit, balance = -balance, 0
        return profit, balance

    def _result_text(self, cfg: dict, profit: int, balance: int) -> str:
        if profit > 0:
            head = tr("🎉 זכית ב-{amount}!", cfg.get("_lang", "he"), amount=self.money(cfg, profit))
        elif profit < 0:
            head = tr("😢 הפסדת {amount}.", cfg.get("_lang", "he"), amount=self.money(cfg, -profit))
        else:
            head = tr("🤝 תיקו, ההימור הוחזר.", cfg.get("_lang", "he"))
        return head + "\n" + tr("יתרה: {amount}", cfg.get("_lang", "he"), amount=self.money(cfg, balance))

    # ---- basic commands -------------------------------------------------------------
    @app_commands.command(name="balance", description=N_("היתרה שלך (או של חבר אחר)"))
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction, member: discord.Member | None = None):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        who = member or interaction.user
        w = self.bot.store.wallet(str(interaction.guild_id), str(who.id), str(who), cfg["start_balance"])
        streak = "\n" + tr("רצף יומי: 🔥 {n}", cfg.get("_lang", "he"), n=w['streak']) if w["streak"] else ""
        await interaction.response.send_message(tr("היתרה של {user}: {amount}", cfg.get("_lang", "he"), user=who.mention, amount=self.money(cfg, w['balance'])) + streak,
                                                allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="daily", description=N_("פרס יומי. ככל שהרצף ארוך יותר, הפרס גדל"))
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        gid, uid, name = self._ids(interaction)
        w = self.bot.store.wallet(gid, uid, name, cfg["start_balance"])
        now = time.time()
        ok, streak, wait = daily_state(w["last_daily"], w["streak"], now)
        if not ok:
            return await interaction.response.send_message(tr("⏳ כבר לקחת היום. אפשר שוב בעוד {time}.", cfg.get("_lang", "he"), time=fmt_time(wait, cfg.get("_lang", "he"))), ephemeral=True)
        amount = daily_amount(streak, cfg["daily_base"], cfg["daily_streak_bonus"], cfg["daily_streak_max"])
        amount = apply_boost(amount, boost_percent(cfg["boosts"], self._roles(interaction.user), "daily", dt.date.today()), "daily")
        balance = self.bot.store.claim_daily(gid, uid, name, now, streak, amount, cfg["start_balance"])
        lost = " " + tr("(הרצף התחיל מחדש)", cfg.get("_lang", "he")) if w["streak"] > 1 and streak == 1 else ""
        await interaction.response.send_message(
            tr("🎁 קיבלת {amount}! רצף: 🔥 {n}", cfg.get("_lang", "he"), amount=self.money(cfg, amount), n=streak) + lost + "\n"
            + tr("יתרה: {amount}", cfg.get("_lang", "he"), amount=self.money(cfg, balance)))

    @app_commands.command(name="pay", description=N_("העברת מטבעות לחבר"))
    @app_commands.guild_only()
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 10**9]):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message(tr("אי אפשר להעביר לבוט או לעצמך.", cfg.get("_lang", "he")), ephemeral=True)
        gid, uid, name = self._ids(interaction)
        if not self.bot.store.transfer(gid, uid, name, str(member.id), str(member), amount, cfg["start_balance"]):
            return await interaction.response.send_message(tr("אין לך מספיק {currency}.", cfg.get("_lang", "he"), currency=cfg["currency_name"]), ephemeral=True)
        await interaction.response.send_message(tr("💸 {a} העביר {amount} ל-{b}.", cfg.get("_lang", "he"), a=interaction.user.mention, amount=self.money(cfg, amount), b=member.mention),
                                                allowed_mentions=discord.AllowedMentions(users=[member]))

    @app_commands.command(name="rich", description=N_("העשירים ביותר בשרת"))
    @app_commands.guild_only()
    async def rich(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        rows = [r for r in self.bot.store.rich_list(str(interaction.guild_id), 10) if r["balance"] > 0]
        if not rows:
            return await interaction.response.send_message(tr("עוד אין לאף אחד מטבעות.", cfg.get("_lang", "he")), ephemeral=True)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [f"{medals.get(i, f'`#{i}`')} <@{r['user_id']}> · {cfg['currency_icon']} {r['balance']:,}" for i, r in enumerate(rows, 1)]
        await interaction.response.send_message(embed=discord.Embed(title=tr("💰 העשירים · {server}", cfg.get("_lang", "he"), server=interaction.guild.name),
                                                                    description="\n".join(lines), color=themed(cfg.get("_lang", "he"), 0xF0B232)))

    # ---- shop -------------------------------------------------------------------------
    @app_commands.command(name="shop", description=N_("החנות של השרת"))
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        if not cfg["items"]:
            return await interaction.response.send_message(tr("החנות ריקה כרגע.", cfg.get("_lang", "he")), ephemeral=True)
        embed = discord.Embed(title=tr("🛒 החנות · {server}", cfg.get("_lang", "he"), server=interaction.guild.name), color=themed(cfg.get("_lang", "he"), 0x6D5DFC))
        gid = str(interaction.guild_id)
        for item in cfg["items"][:25]:
            left = ""
            if item["stock"]:
                left = " · " + tr("נשארו {n}", cfg.get("_lang", "he"), n=max(0, item['stock'] - self.bot.store.item_sold(gid, item['name'].lower())))
            role = "\n" + tr("נותן: {role}", cfg.get("_lang", "he"), role=f"<@&{item['role_id']}>") if item["role_id"] else ""
            embed.add_field(name=f"{item['name']} · {cfg['currency_icon']} {item['price']:,}{left}",
                            value=(item["description"] or "‎") + role, inline=False)
        embed.set_footer(text=tr("קונים עם ‎/buy", cfg.get("_lang", "he")))
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _item_autocomplete(self, interaction: discord.Interaction, current: str):
        cfg = self.bot.cfg(interaction.guild, "economy") or {"items": []}
        return [app_commands.Choice(name=f"{i['name']} ({i['price']:,})"[:100], value=i["name"])
                for i in cfg["items"] if current.lower() in i["name"].lower()][:25]

    @app_commands.command(name="buy", description=N_("קניית פריט מהחנות"))
    @app_commands.guild_only()
    @app_commands.autocomplete(item=_item_autocomplete)
    async def buy(self, interaction: discord.Interaction, item: str):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        found = next((i for i in cfg["items"] if i["name"].lower() == item.strip().lower()), None)
        if found is None:
            return await interaction.response.send_message(tr("הפריט לא נמצא. רשימת הפריטים: ‎/shop", cfg.get("_lang", "he")), ephemeral=True)
        gid, uid, name = self._ids(interaction)
        status, balance = self.bot.store.buy_item(gid, uid, name, found["name"].lower(), found["price"],
                                                  found["stock"], found["per_user"], cfg["start_balance"])
        if status != "ok":
            msg = {"sold_out": tr("הפריט אזל מהמלאי.", cfg.get("_lang", "he")), "limit": tr("הגעת למגבלת הקנייה של הפריט הזה.", cfg.get("_lang", "he")),
                   "funds": tr("אין לך מספיק {currency}. היתרה שלך: {n}.", cfg.get("_lang", "he"), currency=cfg['currency_name'], n=f"{balance:,}")}[status]
            return await interaction.response.send_message(msg, ephemeral=True)
        extra = ""
        if found["role_id"] and isinstance(interaction.user, discord.Member):
            role = interaction.guild.get_role(int(found["role_id"]))
            if can_manage_role(interaction.guild, role):
                try:
                    await interaction.user.add_roles(role, reason=tr("נקנה בחנות: {item}", cfg.get("_lang", "he"), item=found['name']))
                    extra = "\n" + tr("קיבלת את התפקיד **{role}**.", cfg.get("_lang", "he"), role=role.name)
                except discord.HTTPException as exc:
                    log.warning(tr("מתן תפקיד מהחנות נכשל: %s"), exc.text or exc.status)
            else:
                log.warning(tr("פריט '%s': אין לבוט אפשרות לתת את התפקיד"), found["name"])
        reply = f"\n{found['reply']}" if found["reply"] else ""
        await interaction.response.send_message(
            tr("🛍️ קנית **{item}** ב-{amount}.", cfg.get("_lang", "he"), item=found['name'], amount=self.money(cfg, found['price'])) + extra + reply
            + "\n" + tr("יתרה: {amount}", cfg.get("_lang", "he"), amount=self.money(cfg, balance)),
            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="inventory", description=N_("הפריטים שקנית"))
    @app_commands.guild_only()
    async def inventory(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        items = self.bot.store.inventory(str(interaction.guild_id), str(interaction.user.id))
        if not items:
            return await interaction.response.send_message(tr("עוד לא קנית כלום.", cfg.get("_lang", "he")), ephemeral=True)
        names = {i["name"].lower(): i["name"] for i in cfg["items"]}
        lines = [f"• {names.get(r['item'], r['item'])} × {r['qty']}" for r in items]
        await interaction.response.send_message(tr("🎒 הפריטים שלך:", cfg.get("_lang", "he")) + "\n" + "\n".join(lines), ephemeral=True)

    # ---- games ------------------------------------------------------------------------
    @app_commands.command(name="dice", description=N_("קוביות נגד הבוט. מי שמגלגל יותר, מנצח"))
    @app_commands.guild_only()
    async def dice(self, interaction: discord.Interaction, bet: int):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        if not await self._bet_ok(interaction, cfg, "dice", bet):
            return
        me, it = random.randint(1, 6) + random.randint(1, 6), random.randint(1, 6) + random.randint(1, 6)
        profit, balance = self._settle(interaction, cfg, bet if me > it else -bet if me < it else 0)
        await interaction.response.send_message(tr("🎲 אתה: **{a}** · הבוט: **{b}**", cfg.get("_lang", "he"), a=me, b=it) + "\n" + self._result_text(cfg, profit, balance))

    @app_commands.command(name="guess", description=N_("נחש מספר בין 1 ל-10. ניחוש נכון מחזיר פי 8"))
    @app_commands.guild_only()
    async def guess(self, interaction: discord.Interaction, bet: int, number: app_commands.Range[int, 1, 10]):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        if not await self._bet_ok(interaction, cfg, "guess", bet):
            return
        secret = random.randint(1, 10)
        profit, balance = self._settle(interaction, cfg, bet * 7 if secret == number else -bet)
        await interaction.response.send_message(tr("🔢 המספר היה **{n}**.", cfg.get("_lang", "he"), n=secret) + "\n" + self._result_text(cfg, profit, balance))

    @app_commands.command(name="roulette", description=N_("רולטה: אדום או שחור (פי 2), ירוק או מספר (פי 36)"))
    @app_commands.guild_only()
    @app_commands.describe(choice=N_("red, black, green, או מספר בין 0 ל-36"))
    async def roulette(self, interaction: discord.Interaction, bet: int, choice: str):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        choice = ROULETTE_ALIASES.get(choice.strip().lower(), choice.strip().lower())
        if choice not in ("red", "black", "green") and not (choice.isdigit() and 0 <= int(choice) <= 36):
            return await interaction.response.send_message(tr("בחירה לא חוקית: red, black, green, או מספר 0–36.", self.bot.lang(interaction.guild)), ephemeral=True)
        if not await self._bet_ok(interaction, cfg, "roulette", bet):
            return
        number = random.randint(0, 36)
        color = "🟢" if number == 0 else "🔴" if number in RED else "⚫"
        profit, balance = self._settle(interaction, cfg, roulette_profit(choice, number, bet))
        await interaction.response.send_message(tr("🎡 יצא {color} **{n}**", cfg.get("_lang", "he"), color=color, n=number) + "\n" + self._result_text(cfg, profit, balance))

    @app_commands.command(name="rps", description=N_("אבן, נייר ומספריים נגד הבוט"))
    @app_commands.guild_only()
    @app_commands.choices(choice=[app_commands.Choice(name=v, value=k) for k, v in RPS.items()])
    async def rps(self, interaction: discord.Interaction, bet: int, choice: app_commands.Choice[str]):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        if not await self._bet_ok(interaction, cfg, "rps", bet):
            return
        bot_pick = random.choice(list(RPS))
        result = rps_outcome(choice.value, bot_pick)
        profit, balance = self._settle(interaction, cfg, bet * result)
        await interaction.response.send_message(tr("אתה: {a} · הבוט: {b}", cfg.get("_lang", "he"), a=tr(RPS[choice.value], cfg.get("_lang", "he")), b=tr(RPS[bot_pick], cfg.get("_lang", "he"))) + "\n" + self._result_text(cfg, profit, balance))

    # ---- admin --------------------------------------------------------------------------
    async def _admin_change(self, interaction: discord.Interaction, member: discord.Member, delta: int):
        cfg = self._cfg(interaction)
        if not cfg:
            return await self._off(interaction)
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(tr("אין לך הרשאה לפעולה הזו.", cfg.get("_lang", "he")), ephemeral=True)
        bal = self.bot.store.add_coins(str(interaction.guild_id), str(member.id), str(member), delta,
                                       cfg["start_balance"], allow_negative=False)
        if bal is None:
            bal = self.bot.store.wallet(str(interaction.guild_id), str(member.id))["balance"]
            self.bot.store.add_coins(str(interaction.guild_id), str(member.id), str(member), -bal)
            bal = 0
        log.info(tr("%s שינה ל-%s את היתרה ב-%+d"), interaction.user, member, delta)
        await interaction.response.send_message(tr("✅ היתרה של {user}: {amount}", cfg.get("_lang", "he"), user=member.mention, amount=self.money(cfg, bal)), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="give-coins", description=N_("הוספת מטבעות לחבר"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def give_coins(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 10**9]):
        await self._admin_change(interaction, member, amount)

    @app_commands.command(name="remove-coins", description=N_("הורדת מטבעות מחבר"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def remove_coins(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 10**9]):
        await self._admin_change(interaction, member, -amount)
