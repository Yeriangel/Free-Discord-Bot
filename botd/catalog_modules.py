"""Module catalog, laid out like the MEE6 dashboard (categories in their original order).

Each module: key, category, label, description, permissions the bot needs, privileged intents,
status ("ready" or "soon"), slash/text commands it adds, and the settings fields its page shows.
"""

CATEGORIES = [
    ("essentials", "חיוני", "Essentials"),
    ("server_management", "ניהול שרת", "Server Management"),
    ("automations", "אוטומציות", "Automations"),
    ("utilities", "כלים", "Utilities"),
    ("social", "התראות רשתות", "Social Alerts"),
    ("games", "משחקים וכיף", "Games & Fun"),
    ("ai", "בינה מלאכותית", "AI"),
]

LOG_EVENTS = [
    ("message_delete", "הודעה נמחקה"),
    ("message_edit", "הודעה נערכה"),
    ("member_join", "חבר הצטרף"),
    ("member_leave", "חבר עזב"),
    ("member_ban", "חסימה וביטול חסימה"),
    ("member_roles", "שינוי תפקידים"),
    ("member_nick", "שינוי כינוי"),
    ("channels", "ערוצים נוצרו או נמחקו"),
    ("mod_actions", "פעולות ניהול, אזהרות ואוטומוד"),
]

RULE_ACTIONS = [["delete", "מחיקת ההודעה"], ["warn", "אזהרה"], ["both", "מחיקה + אזהרה"]]


def _f(key, type_, label, default=None, **extra) -> dict:
    return {"key": key, "type": type_, "label": label, "default": default, **extra}


def _rule(key, label, desc, on=False, action="delete", params=()) -> dict:
    default = {"on": on, "action": action}
    for p in params:
        default[p["key"]] = p["default"]
    return _f(key, "rule", label, default, hint=desc, actions=RULE_ACTIONS, params=list(params))


def _soon(key, category, label, description, permissions=(), commands=()):
    return {"key": key, "category": category, "label": label, "description": description,
            "permissions": list(permissions), "intents": [], "status": "soon",
            "commands": list(commands), "fields": []}


MODULES = [
    # ================= ESSENTIALS =================
    {
        "key": "welcome", "category": "essentials", "label": "ברוכים הבאים ולהתראות",
        "description": "הודעה כשמישהו מצטרף או עוזב, בטקסט רגיל או ב-Embed",
        "permissions": ["embed_links"], "intents": ["members"], "status": "ready", "commands": [],
        "fields": [
            _f("channel_id", "channel", "ערוץ ברכה", ""),
            _f("welcome_message", "textarea", "הודעת ברוכים הבאים",
               "ברוך הבא {user} לשרת **{server}**! אתה החבר ה-{member_count}.",
               hint="{user} תיוג · {username} שם · {server} שם השרת · {member_count} מספר חברים"),
            _f("use_embed", "bool", "לשלוח כ-Embed (כרטיס צבעוני עם תמונת המשתמש)", False),
            _f("embed_title", "text", "כותרת ה-Embed", "ברוכים הבאים! 👋", max=200),
            _f("embed_color", "text", "צבע ה-Embed", "#6D5DFC", max=7, hint="בפורמט ‎#RRGGBB"),
            _f("dm_welcome", "bool", "לשלוח את הברכה גם בהודעה פרטית", False),
            _f("leave_enabled", "bool", "לשלוח הודעת עזיבה", True),
            _f("leave_channel_id", "channel", "ערוץ עזיבה", "", hint="ריק = ערוץ הברכה"),
            _f("leave_message", "textarea", "הודעת עזיבה", "**{username}** עזב את השרת אחרי {duration}.",
               hint="{duration} = כמה זמן היה בשרת"),
        ],
    },
    {
        "key": "autorole", "category": "essentials", "label": "תפקיד אוטומטי",
        "description": "תפקידים לכל מי שמצטרף", "permissions": ["manage_roles"], "intents": ["members"],
        "status": "ready", "commands": [],
        "fields": [
            _f("role_ids", "roles", "תפקידים שיינתנו", [], hint="תפקיד הבוט חייב להיות מעל התפקידים האלה"),
            _f("include_bots", "bool", "לתת גם לבוטים שמצטרפים", False),
        ],
    },
    {
        "key": "reaction_roles", "category": "essentials", "label": "תפקידים לפי ריאקשן",
        "description": "בחירת תפקיד בלחיצה על ריאקשן", "permissions": ["manage_roles", "add_reactions"],
        "intents": [], "status": "ready", "commands": [],
        "fields": [_f("groups", "reaction_roles", "הודעות תפקידים", [], managed=True)],
    },
    {
        "key": "automod", "category": "essentials", "label": "אוטומוד",
        "description": "כללים אוטומטיים, לכל כלל מתג ופעולה: מחיקה, אזהרה או שתיהן",
        "permissions": ["manage_messages"], "intents": ["message_content"], "status": "ready", "commands": [],
        "fields": [
            _rule("words", "מילים אסורות", "מוחק הודעות עם מילים או ביטויים מהרשימה", params=[
                _f("list", "lines", "מילים אסורות", [], hint="מילה או ביטוי בכל שורה. ההתאמה לא תלויה באותיות גדולות או קטנות."),
            ]),
            _rule("repeat", "טקסט חוזר", "אותה הודעה שנשלחת כמה פעמים ברצף", params=[
                _f("count", "number", "פעמים", 3, min=2, max=10),
            ]),
            _rule("invites", "הזמנות לשרתים", "קישורי discord.gg לשרתים אחרים", on=True),
            _rule("links", "קישורים חיצוניים", "כל קישור, חוץ מדומיינים מותרים", params=[
                _f("allowed", "lines", "דומיינים מותרים", [], hint="למשל youtube.com"),
            ]),
            _rule("caps", "יותר מדי אותיות גדולות", "הודעות באנגלית שרובן באותיות גדולות", params=[
                _f("percent", "number", "אחוז", 70, min=30, max=100),
            ]),
            _rule("emoji", "יותר מדי אימוג'ים", "", params=[_f("limit", "number", "מקסימום", 8, min=1, max=100)]),
            _rule("spoilers", "יותר מדי ספוילרים", "", params=[_f("limit", "number", "מקסימום", 4, min=1, max=50)]),
            _rule("mentions", "יותר מדי תיוגים", "", on=True, params=[_f("limit", "number", "מקסימום", 5, min=1, max=50)]),
            _rule("zalgo", "Zalgo (תווים משובשים)", "טקסט עם הרבה סימנים מצטברים. ניקוד עברי רגיל לא נחשב."),
            _rule("spam", "אנטי-ספאם", "הרבה הודעות בזמן קצר", on=True, action="both", params=[
                _f("count", "number", "הודעות", 5, min=2, max=30),
                _f("seconds", "number", "בתוך שניות", 5, min=2, max=60),
            ]),
            _f("ignore_bots", "bool", "להתעלם מבוטים אחרים", True),
            _f("exempt_role_ids", "roles", "תפקידים פטורים", [], hint="מי שיש לו 'ניהול הודעות' פטור תמיד"),
            _f("ignored_channel_ids", "channels", "ערוצים שלא נבדקים", []),
        ],
    },
    {
        "key": "warnings", "category": "essentials", "label": "אזהרות ופעולות אוטומטיות",
        "description": "אזהרות ידניות ומהאוטומוד, עם הסלמה אוטומטית: השתקה, הרחקה או חסימה",
        "permissions": ["moderate_members", "kick_members", "ban_members"], "intents": [], "status": "ready",
        "commands": ["/warn", "/warnings", "/unwarn", "/clearwarns"],
        "fields": [
            _f("dm_user", "bool", "לשלוח למשתמש הודעה פרטית כשהוא מקבל אזהרה", True),
            _f("window_days", "number", "לספור אזהרות מהימים האחרונים", 30, min=0, max=3650, hint="0 = כל הזמן"),
            _f("steps", "list", "פעולות אוטומטיות", [{"count": 3, "action": "timeout", "minutes": 60}],
               hint="כשמשתמש מגיע למספר האזהרות, הפעולה מתבצעת אוטומטית, בלי אישור ידני.",
               max_items=10, item_fields=[
                   _f("count", "number", "אחרי אזהרות", 3, min=1, max=50),
                   _f("action", "select", "פעולה", "timeout", options=[
                       ["timeout", "השתקה זמנית"], ["kick", "הרחקה"], ["tempban", "חסימה זמנית"], ["ban", "חסימה"]]),
                   _f("minutes", "number", "דקות (להשתקה או חסימה זמנית)", 60, min=1, max=40320),
               ]),
        ],
    },
    {
        "key": "restricted_channels", "category": "essentials", "label": "ערוצים מוגבלים",
        "description": "ערוצי 'תמונות בלבד', 'וידאו בלבד' או 'פקודות בלבד'. שאר ההודעות נמחקות.",
        "permissions": ["manage_messages"], "intents": ["message_content"], "status": "ready", "commands": [],
        "fields": [
            _f("channels", "list", "ערוצים", [], max_items=25, item_fields=[
                _f("channel_id", "channel", "ערוץ", "", required=True),
                _f("mode", "select", "מה מותר", "images", options=[
                    ["images", "תמונות בלבד"], ["videos", "וידאו בלבד"], ["media", "תמונות או וידאו"],
                    ["commands", "פקודות בלבד"]]),
            ]),
            _f("command_prefixes", "lines", "קידומות של פקודות", ["!"], hint="למצב 'פקודות בלבד'"),
            _f("reply_in_channel", "bool", "להציג הודעה קצרה בערוץ (נמחקת אחרי כמה שניות)", True),
            _f("notify_dm", "bool", "לשלוח לכותב הודעה פרטית עם הסבר", False),
        ],
    },
    {
        "key": "mod_commands", "category": "essentials", "label": "פקודות ניהול",
        "description": "פקודות סלאש לניהול. הרחקה, חסימה, השתקה ומחיקה מציגות קודם כפתור אישור.",
        "permissions": ["kick_members", "ban_members", "moderate_members", "manage_messages"],
        "intents": [], "status": "ready",
        "commands": ["/kick", "/ban", "/unban", "/timeout", "/untimeout", "/purge"],
        "fields": [_f("dm_user", "bool", "לשלוח הודעה פרטית למשתמש לפני הרחקה או חסימה", True)],
    },
    {
        "key": "logs", "category": "essentials", "label": "לוג פעולות",
        "description": "תיעוד אירועים ופעולות ניהול בערוץ לוגים", "permissions": ["view_audit_log"],
        "intents": ["members", "message_content"], "status": "ready", "commands": [],
        "fields": [_f("channel_id", "channel", "ערוץ לוגים", "")] + [_f(k, "bool", l, True) for k, l in LOG_EVENTS],
    },
    {
        "key": "levels", "category": "essentials", "label": "רמות",
        "description": "XP על הודעות, הודעת עליית רמה, תפקידים כפרס, מכפיל XP ולוח מובילים",
        "permissions": ["manage_roles"], "intents": [], "status": "ready",
        "commands": ["/rank", "/levels", "/give-xp", "/remove-xp"],
        "fields": [
            _f("announce_mode", "select", "איפה להודיע על עליית רמה", "current", options=[
                ["current", "בערוץ שבו נכתבה ההודעה"], ["channel", "בערוץ קבוע"], ["dm", "בהודעה פרטית"], ["off", "לא להודיע"]]),
            _f("announce_channel_id", "channel", "ערוץ קבוע להודעות", "", hint="רק כשנבחר 'בערוץ קבוע'"),
            _f("announce_message", "textarea", "הודעת עליית רמה", "🎉 כל הכבוד {player}, עלית לרמה **{level}**!",
               hint="{player} תיוג · {player_name} שם · {level} הרמה החדשה · {server} שם השרת"),
            _f("rate", "select", "קצב XP", "1", options=[
                ["0.25", "x0.25"], ["0.5", "x0.5"], ["0.75", "x0.75"], ["1", "x1 (רגיל)"], ["1.5", "x1.5"],
                ["2", "x2"], ["2.5", "x2.5"], ["3", "x3"]],
               hint="15–25 XP להודעה, לכל היותר פעם בדקה, כפול הקצב"),
            _f("rewards", "list", "תפקידים כפרס", [], max_items=50, item_fields=[
                _f("level", "number", "ברמה", 5, min=1, max=500),
                _f("role_id", "role", "תפקיד", "", required=True),
            ]),
            _f("reward_mode", "select", "איך לתת פרסים", "stack", options=[
                ["stack", "לצבור: לשמור את כל התפקידים שהושגו"], ["highest", "רק התפקיד של הרמה הגבוהה ביותר"]]),
            _f("remove_on_loss", "bool", "להסיר תפקידי פרס כשמשתמש מאבד XP (‎/remove-xp)", True),
            _f("roles_mode", "select", "תפקידים בלי XP", "deny", options=[
                ["deny", "לחסום רק את התפקידים ברשימה"], ["allow", "לתת XP רק לתפקידים ברשימה"]]),
            _f("no_xp_role_ids", "roles", "רשימת תפקידים", []),
            _f("channels_mode", "select", "ערוצים בלי XP", "deny", options=[
                ["deny", "לחסום רק את הערוצים ברשימה"], ["allow", "לתת XP רק בערוצים ברשימה"]]),
            _f("no_xp_channel_ids", "channels", "רשימת ערוצים", []),
            _f("card_color", "text", "צבע כרטיס הדירוג", "#6D5DFC", max=7, hint="בפורמט ‎#RRGGBB"),
        ],
    },
    _soon("starboard", "essentials", "Starboard", "הודעות פופולריות מודגשות בערוץ ייעודי", ["read_message_history"]),

    # ================= SERVER MANAGEMENT =================
    {
        "key": "custom_commands", "category": "server_management", "label": "פקודות מותאמות",
        "description": "פקודות טקסט עם תשובה שאתה כותב. פקודה יכולה גם לתת או להוריד תפקיד.",
        "permissions": ["manage_roles"], "intents": ["message_content"], "status": "ready", "commands": [],
        "fields": [
            _f("prefix", "text", "קידומת", "!", max=5),
            _f("commands", "commands", "פקודות", [],
               hint="בתשובה אפשר להשתמש ב-{user}, {username}, {server}, {channel}"),
        ],
    },
    {
        "key": "automations", "category": "automations", "label": "אוטומציות",
        "description": "כשמישהו עושה X, אם מתקיימים תנאים, עשה Y: הודעות, ריאקשנים, כפתורים, תפקידים, קול, מטבעות ו-XP. עד 50 אוטומציות.",
        "permissions": ["manage_roles", "manage_messages", "add_reactions", "create_public_threads", "send_messages_in_threads"],
        "intents": ["members", "message_content"], "status": "ready", "commands": [],
        "fields": [_f("items", "automations", "אוטומציות", [])],
    },
    _soon("invite_tracker", "server_management", "מעקב הזמנות", "מי הזמין כמה אנשים, עם לוח מובילים", ["manage_guild"]),
    _soon("ticketing", "server_management", "כרטיסי תמיכה", "פניות, דיווחים ובקשות בערוץ פרטי", ["manage_channels"]),
    _soon("suggestions", "server_management", "תיבת הצעות", "הצעות עם הצבעה ושרשור דיון", ["add_reactions", "manage_threads"]),

    # ================= UTILITIES =================
    _soon("polls", "utilities", "סקרים", "סקרים עם הצבעה"),
    _soon("embed_messages", "utilities", "הודעות Embed", "הודעות מעוצבות עם צבעים ותמונות"),
    _soon("reminders", "utilities", "תזכורות", "הודעות חוזרות כל כמה דקות או שעות"),
    _soon("stats", "utilities", "ערוצי סטטיסטיקה", "מספר חברים ועוד בשמות ערוצים", ["manage_channels", "connect"]),
    _soon("temp_channels", "utilities", "ערוצים זמניים", "ערוץ קול שנוצר ונמחק אוטומטית", ["manage_channels", "move_members", "connect"]),
    _soon("afk", "utilities", "סטטוס AFK", "הודעה אוטומטית כשמתייגים מישהו שלא נמצא", ["manage_nicknames"]),

    # ================= SOCIAL ALERTS =================
    _soon("social_alerts", "social", "התראות רשתות", "YouTube, Twitch, TikTok, X, Reddit, RSS ועוד"),

    # ================= GAMES & FUN =================
    {
        "key": "economy", "category": "games", "label": "כלכלה",
        "description": "מטבע מותאם, ‎/daily עם רצפים, משחקים, חנות פריטים ו-Boosts",
        "permissions": ["manage_roles"], "intents": [], "status": "ready",
        "commands": ["/balance", "/daily", "/pay", "/rich", "/shop", "/buy", "/inventory",
                     "/dice", "/guess", "/roulette", "/rps", "/give-coins", "/remove-coins"],
        "fields": [
            _f("currency_name", "text", "שם המטבע", "מטבעות", max=30),
            _f("currency_icon", "text", "אייקון המטבע", "🪙", max=64, hint="אימוג'י, או אימוג'י של השרת בצורה <:name:id>"),
            _f("start_balance", "number", "יתרת פתיחה", 0, min=0, max=1_000_000),
            _f("daily_base", "number", "פרס יומי בסיסי", 100, min=1, max=1_000_000),
            _f("daily_streak_bonus", "number", "תוספת לכל יום ברצף", 10, min=0, max=100_000),
            _f("daily_streak_max", "number", "מקסימום ימי תוספת", 30, min=0, max=365,
               hint="הרצף נשמר אם לוקחים את הפרס בתוך 48 שעות מהפעם הקודמת"),
            _f("dice_on", "bool", "🎲 קוביות (‎/dice)", True),
            _f("guess_on", "bool", "🔢 ניחוש מספר (‎/guess)", True),
            _f("roulette_on", "bool", "🎡 רולטה (‎/roulette)", True),
            _f("rps_on", "bool", "✂️ אבן נייר ומספריים (‎/rps)", True),
            _f("min_bet", "number", "הימור מינימלי", 10, min=1, max=1_000_000),
            _f("max_bet", "number", "הימור מקסימלי", 5000, min=1, max=10**9),
            _f("items", "list", "פריטים בחנות", [], max_items=25, unique="name", item_fields=[
                _f("name", "text", "שם", "", required=True, max=50),
                _f("price", "number", "מחיר", 100, min=0, max=10**9),
                _f("description", "text", "תיאור", "", max=200),
                _f("role_id", "role", "נותן תפקיד", ""),
                _f("reply", "text", "תגובה אחרי קנייה", "", max=300),
                _f("stock", "number", "מלאי (0 = ללא הגבלה)", 0, min=0, max=1_000_000),
                _f("per_user", "number", "מקסימום למשתמש (0 = ללא)", 0, min=0, max=1000),
            ]),
            _f("boosts", "list", "Boosts", [], max_items=20,
               hint="בונוס באחוזים. לפרס היומי ולזכיות הוא מוסיף, ובהפסדים הוא מקטין את ההפסד. "
                    "בלי תפקיד = לכולם. בלי תאריך = קבוע.",
               item_fields=[
                   _f("target", "select", "על מה", "daily", options=[
                       ["daily", "פרס יומי"], ["winnings", "זכיות במשחקים"], ["losses", "הקטנת הפסדים"]]),
                   _f("percent", "number", "אחוז", 10, min=1, max=500),
                   _f("role_id", "role", "לתפקיד (לא חובה)", ""),
                   _f("until", "date", "עד תאריך (לא חובה)", "", hint="YYYY-MM-DD"),
               ]),
        ],
    },
    _soon("giveaways", "games", "הגרלות", "הגרלות עם זוכה אקראי", ["add_reactions"]),
    _soon("birthdays", "games", "ימי הולדת", "ברכה ותפקיד ביום ההולדת", ["manage_roles"]),

    # ================= AI =================
    {
        "key": "claude_chat", "category": "ai", "label": "צ'אט עם Claude",
        "description": "Claude עונה למשתמש המורשה בלבד: בהודעה פרטית, כשמתייגים את הבוט או כשעונים לו",
        "permissions": ["read_message_history"], "intents": [], "status": "ready", "commands": ["/claude_reset"],
        "fields": [
            _f("system_prompt", "textarea", "הנחיות ל-Claude (System prompt)",
               "אתה עוזר ידידותי בשרת דיסקורד. ענה בשפה שבה פנו אליך, בקצרה ובבהירות. "
               "השתמש ב-Markdown של דיסקורד כשזה עוזר.",
               hint="איך Claude מתנהג: אופי, שפה, אורך תשובות"),
            _f("model", "select", "מודל", "haiku", options=[
                ["haiku", "Haiku: הכי מהיר וזול (מומלץ לצ'אט)"],
                ["sonnet", "Sonnet: חכם יותר, בערך פי 20 מהמחיר של Haiku"],
                ["opus", "Opus: הכי חכם, בערך פי 40 מהמחיר של Haiku"],
            ], hint="מודל חזק יותר עונה טוב יותר, אבל כל הודעה עולה יותר טוקנים. מחיר למיליון טוקנים (נכנסים / יוצאים): Haiku ‏$0.10 / $0.50 · Sonnet ‏$2 / $10 · Opus ‏$4 / $20"),
            _f("effort", "select", "רמת מאמץ", "low", options=[
                ["low", "נמוכה: מהיר וזול (מומלץ לצ'אט)"], ["medium", "בינונית"], ["high", "גבוהה: יסודי יותר"]]),
            _f("history_messages", "number", "כמה הודעות קודמות לזכור", 10, min=0, max=30,
               hint="לכל ערוץ בנפרד. הזיכרון נמחק כשהבוט נכבה, או עם ‎/claude_reset. ‏0 = בלי זיכרון."),
            _f("allow_actions", "bool", "לאפשר ל-Claude לבצע פעולות בשרת (תמיד עם כפתור אישור)", False,
               hint="למשל: השתקה, הרחקה, חסימה, תפקידים, שינוי שמות ערוצים, מחיקת הודעות עם מילים מסוימות ושליחת הודעות. "
                    "רק אחרי שהמשתמש המורשה לוחץ 'אשר', ורק אם יש לו בעצמו הרשאה לפעולה."),
            _f("reply_without_mention", "bool", "בערוץ המורשה: לענות לכל הודעה, גם בלי תיוג", False,
               hint="עובד רק אם הוגדר ערוץ מורשה, ודורש Message Content Intent"),
            _f("cooldown_seconds", "number", "המתנה בין הודעות (שניות)", 3, min=0, max=300),
            _f("daily_limit", "number", "מגבלת תשובות ליום", 200, min=0, max=100000, hint="0 = בלי מגבלה"),
        ],
    },
]

# Modules that were placeholders are now built: swap them in, keeping the order above.
from .catalog_new import NEW_MODULES  # noqa: E402

MODULES = [NEW_MODULES.get(m["key"], m) for m in MODULES]
