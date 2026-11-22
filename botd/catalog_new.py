"""Catalog entries for the modules that used to be "coming soon". They replace the placeholders in
catalog_modules.MODULES by key, so the MEE6 category order stays the same."""


def _f(key, type_, label, default=None, **extra) -> dict:
    return {"key": key, "type": type_, "label": label, "default": default, **extra}


def _m(key, category, label, description, fields, permissions=(), intents=(), commands=()) -> dict:
    return {"key": key, "category": category, "label": label, "description": description,
            "permissions": list(permissions), "intents": list(intents), "status": "ready",
            "commands": list(commands), "fields": fields}


NEW_MODULES = {m["key"]: m for m in [
    _m("starboard", "essentials", "Starboard", "הודעות פופולריות מודגשות בערוץ ייעודי", [
        _f("channel_id", "channel", "ערוץ ה-Starboard", ""),
        _f("emoji", "text", "אימוג'י", "⭐", max=64, hint="אימוג'י רגיל, או של השרת בצורה <:name:id>"),
        _f("threshold", "number", "כמה ריאקשנים צריך", 3, min=1, max=100),
        _f("self_star", "bool", "לספור גם ריאקשן של כותב ההודעה", False),
        _f("ignored_channel_ids", "channels", "ערוצים שלא נכנסים ל-Starboard", []),
    ], permissions=["read_message_history", "embed_links"], intents=["message_content"]),

    _m("invite_tracker", "server_management", "מעקב הזמנות", "מי הזמין כמה אנשים, עם לוח מובילים", [
        _f("channel_id", "channel", "ערוץ הודעות הצטרפות", "", hint="ריק = בלי הודעה, רק ספירה"),
        _f("join_message", "textarea", "הודעה כשמישהו מצטרף",
           "{user} הצטרף. הוזמן על ידי {inviter}, שהזמין כבר {invites} אנשים.",
           hint="{user} המצטרף · {username} שם · {inviter} מי שהזמין · {invites} מספר ההזמנות שלו · {code} קוד ההזמנה"),
        _f("unknown_message", "textarea", "הודעה כשלא ידוע מי הזמין", "{user} הצטרף. לא הצלחתי לזהות מי הזמין."),
    ], permissions=["manage_guild"], intents=["members"], commands=["/invites", "/invites-top"]),

    _m("ticketing", "server_management", "כרטיסי תמיכה", "פניות, דיווחים ובקשות בשרשור פרטי עם הצוות", [
        _f("panel_channel_id", "channel", "ערוץ הכפתור לפתיחת כרטיס", "",
           hint="אחרי השמירה לחץ 'פרסם כפתור' או השתמש ב-‎/ticket-panel. הכרטיסים נפתחים כשרשורים פרטיים בערוץ הזה."),
        _f("panel_title", "text", "כותרת", "צריך עזרה? 🎫", max=200),
        _f("panel_message", "textarea", "טקסט ההודעה", "לחץ על הכפתור כדי לפתוח כרטיס פרטי עם הצוות."),
        _f("button_label", "text", "טקסט הכפתור", "פתח כרטיס", max=80),
        _f("support_role_ids", "roles", "תפקידי צוות שמצטרפים לכרטיסים", []),
        _f("welcome_message", "textarea", "הודעה בתוך כרטיס חדש", "שלום {user}! תאר את הבקשה שלך, והצוות יענה בהקדם.",
           hint="{user} מי שפתח · {number} מספר הכרטיס"),
        _f("max_open", "number", "כמה כרטיסים פתוחים מותר לכל משתמש", 1, min=1, max=5),
        _f("log_channel_id", "channel", "ערוץ לוג לכרטיסים (לא חובה)", ""),
    ], permissions=["create_private_threads", "send_messages_in_threads", "manage_threads"], commands=["/ticket-panel", "/ticket-close"]),

    _m("suggestions", "server_management", "תיבת הצעות", "הצעות עם הצבעה ושרשור דיון", [
        _f("channel_id", "channel", "ערוץ ההצעות", ""),
        _f("convert_messages", "bool", "להפוך כל הודעה שנכתבת בערוץ ההצעות להצעה", True,
           hint="דורש Message Content Intent. בכל מקרה אפשר להשתמש ב-‎/suggest."),
        _f("create_thread", "bool", "לפתוח שרשור דיון לכל הצעה", True),
        _f("anonymous", "bool", "להסתיר את שם המציע", False),
        _f("reviewer_role_ids", "roles", "מי יכול לאשר או לדחות הצעות", [], hint="מי שיש לו 'ניהול הודעות' יכול תמיד"),
    ], permissions=["add_reactions", "create_public_threads", "embed_links", "manage_messages"], intents=["message_content"],
        commands=["/suggest", "/suggestion"]),

    _m("polls", "utilities", "סקרים", "סקרים של דיסקורד עם הצבעה ותוצאות חיות", [
        _f("allow_everyone", "bool", "כל חבר יכול ליצור סקר", False),
        _f("creator_role_ids", "roles", "תפקידים שיכולים ליצור סקרים", [], hint="מי שיש לו 'ניהול הודעות' יכול תמיד"),
        _f("max_hours", "number", "משך מקסימלי לסקר (שעות)", 168, min=1, max=768),
    ], permissions=["send_polls"], commands=["/poll"]),

    _m("embed_messages", "utilities", "הודעות Embed", "הודעות מעוצבות עם צבעים ותמונות, נשלחות מהאפליקציה", [
        _f("channel_id", "channel", "ערוץ", ""),
        _f("title", "text", "כותרת", "", max=256),
        _f("description", "textarea", "תוכן", "", hint="אפשר Markdown של דיסקורד"),
        _f("color", "text", "צבע", "#6D5DFC", max=7, hint="בפורמט ‎#RRGGBB"),
        _f("image_url", "text", "קישור לתמונה גדולה (לא חובה)", "", max=500),
        _f("thumbnail_url", "text", "קישור לתמונה קטנה (לא חובה)", "", max=500),
        _f("footer", "text", "שורה תחתונה (לא חובה)", "", max=200),
    ], permissions=["embed_links"]),

    _m("reminders", "utilities", "תזכורות", "הודעות חוזרות כל כמה דקות או שעות, ותזכורות אישיות", [
        _f("items", "list", "הודעות חוזרות", [], max_items=25, item_fields=[
            _f("channel_id", "channel", "ערוץ", "", required=True),
            _f("text", "text", "הודעה", "", required=True, max=1000),
            _f("every_minutes", "number", "כל כמה דקות", 60, min=5, max=43200),
        ]),
        _f("allow_personal", "bool", "לאפשר לכולם ‎/remind (תזכורת אישית)", True),
        _f("max_per_user", "number", "מקסימום תזכורות פתוחות למשתמש", 10, min=1, max=50),
    ], commands=["/remind", "/reminders", "/reminder-delete"]),

    _m("stats", "utilities", "ערוצי סטטיסטיקה", "מספר חברים ועוד בשמות ערוצים", [
        _f("category_name", "text", "שם הקטגוריה", "📊 סטטיסטיקה", max=90),
        _f("counters", "list", "מונים", [{"kind": "members", "name": "👥 חברים: {count}"}], max_items=6, item_fields=[
            _f("kind", "select", "מה לספור", "members", options=[
                ["members", "כל החברים"], ["humans", "אנשים (בלי בוטים)"], ["bots", "בוטים"], ["channels", "ערוצים"],
                ["roles", "תפקידים"], ["boosts", "בוסטים"]]),
            _f("name", "text", "שם הערוץ", "👥 חברים: {count}", required=True, max=90, hint="{count} = המספר"),
        ], hint="הבוט יוצר ערוצי קול נעולים ומעדכן אותם כל 10 דקות (דיסקורד מגביל שינויי שם)."),
    ], permissions=["manage_channels", "connect"], intents=["members"]),

    _m("temp_channels", "utilities", "ערוצים זמניים", "ערוץ קול שנוצר ונמחק אוטומטית", [
        _f("lobby_channel_id", "voice_channel", "ערוץ הקול 'הצטרף כדי ליצור'", "",
           hint="מי שנכנס לערוץ הזה מקבל ערוץ קול משלו, שנמחק כשכולם יוצאים"),
        _f("name_template", "text", "שם הערוץ החדש", "🔊 הערוץ של {username}", max=90),
        _f("user_limit", "number", "מגבלת משתתפים (0 = ללא)", 0, min=0, max=99),
        _f("owner_can_manage", "bool", "יוצר הערוץ יכול לשנות את השם וההגבלות", True),
    ], permissions=["manage_channels", "move_members", "connect"]),

    _m("afk", "utilities", "סטטוס AFK", "הודעה אוטומטית כשמתייגים מישהו שלא נמצא", [
        _f("reply_message", "text", "התשובה כשמתייגים מישהו AFK", "💤 {username} לא נמצא כרגע: {reason} ({since})", max=300,
           hint="{username} שם · {reason} הסיבה · {since} מתי יצא"),
        _f("nickname_prefix", "bool", "להוסיף [AFK] לכינוי", True),
        _f("welcome_back", "bool", "להודיע כשמישהו חוזר", True),
    ], permissions=["manage_nicknames"], commands=["/afk"]),

    _m("social_alerts", "social", "התראות רשתות", "YouTube, Twitch, Reddit ו-RSS", [
        _f("feeds", "list", "מקורות", [], max_items=25, item_fields=[
            _f("kind", "select", "רשת", "youtube", options=[
                ["youtube", "YouTube"], ["twitch", "Twitch"], ["reddit", "Reddit"], ["rss", "RSS / אתר"]]),
            _f("source", "text", "ערוץ / משתמש / קישור", "", required=True, max=300,
               hint="YouTube: קישור לערוץ או ‎@handle · Twitch: שם משתמש · Reddit: שם הסאב · RSS: קישור לפיד"),
            _f("channel_id", "channel", "ערוץ ההתראות", "", required=True),
            _f("message", "text", "הודעה", "📣 {name}: {title}\n{url}", max=500, hint="{name} · {title} · {url}"),
            _f("role_id", "role", "תפקיד לתייג (לא חובה)", ""),
        ]),
        _f("check_minutes", "number", "לבדוק כל כמה דקות", 10, min=5, max=120),
    ], commands=[]),

    _m("giveaways", "games", "הגרלות", "הגרלות עם כפתור השתתפות וזוכה אקראי", [
        _f("manager_role_ids", "roles", "מי יכול ליצור הגרלות", [], hint="מי שיש לו 'ניהול השרת' יכול תמיד"),
        _f("button_label", "text", "טקסט הכפתור", "🎉 השתתף", max=80),
        _f("dm_winners", "bool", "לשלוח לזוכים הודעה פרטית", True),
    ], permissions=["embed_links"], commands=["/giveaway start", "/giveaway end", "/giveaway reroll", "/giveaway list"]),

    _m("birthdays", "games", "ימי הולדת", "ברכה ותפקיד ביום ההולדת", [
        _f("channel_id", "channel", "ערוץ הברכות", ""),
        _f("message", "textarea", "הודעת הברכה", "🎂 יום הולדת שמח {user}! 🎉",
           hint="{user} תיוג (או שם) · {username} שם · {age} הגיל, אם נרשמה שנה"),
        _f("role_id", "role", "תפקיד ליום ההולדת (לא חובה)", "", hint="ניתן בבוקר ומוסר למחרת"),
        _f("hour", "number", "באיזו שעה לברך (לפי השעון של המחשב)", 9, min=0, max=23),
        _f("allow_self", "bool", "כל חבר יכול לרשום את יום ההולדת שלו (‎/birthday set)", True),
    ], permissions=["manage_roles"], commands=["/birthday set", "/birthday remove", "/birthday list", "/birthday add"]),
]}
