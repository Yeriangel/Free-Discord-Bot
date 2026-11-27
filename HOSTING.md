# Run the bot 24/7 on a server

On your PC, the bot works only while the PC is on. To keep it online all the time, run it on a computer that is
always on: a small cloud server (VPS), a Raspberry Pi, or an old PC at home. The bot itself is the same, only the
window is not needed there.

[עברית למטה](#הרצת-הבוט-247-על-שרת)

> [!IMPORTANT]
> Run the bot in **one place only**. If the same bot token runs on your PC and on a server at the same time, every
> message gets two answers. After the server works, turn off "Start the bot automatically" in the app on your PC and
> stop the bot there.

## What you need

- A server with Linux (Ubuntu or Debian is easiest). Many providers have a free or very cheap small server; 1 GB of
  memory is enough. Pick one yourself; this guide works with any of them.
- Your keys: the Bot Token, and optionally your AI keys (Claude / ChatGPT / Grok), Twitch and Roblox.

## Step 1: back up your settings on the PC

In the app: **Connection & Settings → Run 24/7 on a server → Create a backup**. A zip appears in your Downloads folder.
It has every server's settings and data (levels, birthdays, warnings…) and **no keys or tokens**.

## Step 2 (option A): Docker, the easiest way

On the server:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
git clone https://github.com/Yeriangel/Free-Discord-Bot.git
cd Free-Discord-Bot
cp .env.example .env
nano .env          # paste your Bot Token after BOTD_BOT_TOKEN= (and any other keys), then Ctrl+O, Enter, Ctrl+X
```

Copy the backup from your PC to the server (for example with `scp FreeDiscordBot-backup-....zip user@SERVER:~/`), then:

```bash
sudo docker compose build
sudo docker compose run --rm -v ~/FreeDiscordBot-backup-....zip:/backup.zip bot python -m botd import /backup.zip
sudo docker compose up -d
sudo docker compose logs -f        # the bot should say "Connected as ..."; Ctrl+C to stop watching
```

(Put the real file name of your backup instead of `FreeDiscordBot-backup-....zip`.)

The bot now starts by itself, also after the server restarts.

## Step 2 (option B): without Docker

```bash
sudo apt update && sudo apt install -y python3 python3-venv ffmpeg git
git clone https://github.com/Yeriangel/Free-Discord-Bot.git
cd Free-Discord-Bot
python3 -m venv .venv && .venv/bin/pip install -r requirements-server.txt
.venv/bin/python -m botd import ~/FreeDiscordBot-backup-....zip
```

Make it run all the time with systemd. Create `/etc/systemd/system/freediscordbot.service`:

```ini
[Unit]
Description=Free Discord Bot
After=network-online.target

[Service]
User=YOUR_LINUX_USER
WorkingDirectory=/home/YOUR_LINUX_USER/Free-Discord-Bot
Environment=BOTD_PORT=8765
Environment=BOTD_AUTOSTART=1
EnvironmentFile=/home/YOUR_LINUX_USER/Free-Discord-Bot/.env
ExecStart=/home/YOUR_LINUX_USER/Free-Discord-Bot/.venv/bin/python -m botd
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
cp .env.example .env && nano .env      # your keys
sudo systemctl enable --now freediscordbot
journalctl -u freediscordbot -f        # watch the log
```

## Step 2 (option C): a free Discord bot hosting site (no server of your own)

Sites with a "Python" server and a Pterodactyl panel (for example MonkeyBytes, Wispbyte) work too. On the PC:

```powershell
.venv\Scripts\python tools\make_host_package.py
```

It makes `dist\hosting\FreeDiscordBot-hosting.zip` with the bot, your settings (no keys) and a start file
(`main.py`). Upload it in the site's File Manager, extract it, put your keys in `secrets.env`, and press Start.
The full steps are in `STEPS.txt` inside the zip. These sites have no ffmpeg, so /gif, /opacity and
/file-format do not work there.

**Updates on hosting sites are automatic:** the start file checks this project's GitHub releases when it starts
and every 3 hours, installs a newer version (your `secrets.env` and `data` folder stay) and restarts the bot by itself.
To turn it off, add `BOTD_AUTO_UPDATE=0` to `secrets.env`.

## Step 3: the dashboard, from your PC

The dashboard is not open to the internet (on purpose: only you should be able to control the bot). Open it through
an SSH tunnel. On your PC, in PowerShell:

```powershell
ssh -L 8765:127.0.0.1:8765 user@SERVER
```

Keep that window open. In a second window on the server, get the link:

```bash
sudo docker compose exec bot python -m botd url     # Docker
.venv/bin/python -m botd url                        # without Docker
```

Open the link it prints (`http://127.0.0.1:8765/#token=...`) in your browser on the PC. That is the same dashboard as
in the app. Keys are not changed from there; they live in the `.env` file on the server.

## Use your Android phone as a backup (Termux)

Your phone can keep the bot online while the PC is off. It runs in **standby**: every 30 seconds it asks your PC
whether the PC's bot is online. When the PC's bot is off or the PC does not answer, the phone starts the bot; as soon
as the PC's bot is back, the phone stops it again. (For up to about 30 seconds both may answer once.)

> [!NOTE]
> This runs the real bot on the phone, so the phone needs your Bot Token and AI keys in a `.env` file. Use a phone
> only you use. Changes made while the phone runs the bot (levels, birthdays…) stay on the phone; they are not copied
> back to the PC.

1. On the PC app: **Connection & Settings → Phone app**, turn on **Phone access** (the phone checks the PC through it),
   and note the Tailscale address shown there (`100.x.y.z`).
2. On the phone, install **Tailscale** (log in with the same account as the PC) and **Termux** from F-Droid
   (the Play Store version is outdated).
3. In Termux:

```bash
pkg update && pkg install -y python git rust binutils ffmpeg tzdata termux-api
git clone https://github.com/Yeriangel/Free-Discord-Bot.git
cd Free-Discord-Bot
pip install -r requirements-termux.txt          # the first time can take 10-30 minutes
cp .env.example .env && nano .env                # your Bot Token and keys
```

4. Copy the settings: create a backup in the PC app, put the zip in the phone's Downloads, then:

```bash
termux-setup-storage
python -m botd import ~/storage/downloads/FreeDiscordBot-backup-....zip
```

5. Start the backup (keep Termux open in the background):

```bash
termux-wake-lock
set -a; . ./.env; set +a
BOTD_STANDBY_URL=http://100.x.y.z:8765 python -m botd    # the PC's Tailscale address and phone port
```

In Android settings, turn off battery optimization for Termux, or Android may stop it.

## Good to know

- **Updates:** `git pull` and then `sudo docker compose up -d --build` (Docker) or `sudo systemctl restart freediscordbot`.
- **/gif, /opacity, /file-format** need ffmpeg; Docker already has it, option B installs it above.
- **/time** uses the server's time zones (names like `Asia/Jerusalem`); `GMT+3` works too.
- **Back to the PC:** stop the bot on the server first (`sudo docker compose down`), then start it in the app.

---

## הרצת הבוט 24/7 על שרת

במחשב שלך הבוט עובד רק כשהמחשב דלוק. כדי שיהיה מחובר כל הזמן, מריצים אותו על מחשב שתמיד דלוק: שרת ענן קטן (VPS),
Raspberry Pi או מחשב ישן בבית. הבוט זהה, רק בלי החלון.

> [!IMPORTANT]
> מריצים את הבוט **רק במקום אחד**. אם אותו טוקן רץ גם במחשב וגם בשרת, כל הודעה תקבל שתי תשובות. אחרי שהשרת עובד,
> מכבים באפליקציה במחשב את "להפעיל את הבוט אוטומטית" ועוצרים את הבוט שם.

1. **גיבוי במחשב:** באפליקציה, **חיבור והגדרות ← הרצה 24/7 על שרת ← צור גיבוי**. נוצר קובץ zip בתיקיית ההורדות, עם
   ההגדרות והנתונים של כל השרתים, **בלי מפתחות וטוקנים**.
2. **בשרת:** עוקבים אחרי שלב 2 למעלה (Docker הכי פשוט). את הטוקן והמפתחות שמים בקובץ `.env` בשרת, לא באפליקציה.
3. **לוח הבקרה:** לא פתוח לאינטרנט, בכוונה. פותחים מנהרת SSH מהמחשב (`ssh -L 8765:127.0.0.1:8765 user@SERVER`),
   מקבלים קישור עם `python -m botd url`, ופותחים אותו בדפדפן במחשב.

עדכונים: `git pull` ואז `sudo docker compose up -d --build`.

**אתר אחסון חינמי לבוטים (בלי שרת משלך):** במחשב מריצים `.venv\Scripts\python tools\make_host_package.py`, ומקבלים את
`dist\hosting\FreeDiscordBot-hosting.zip`. מעלים אותו ב-File Manager של האתר, מחלצים, שמים את המפתחות ב-`secrets.env`
ולוחצים Start. כל השלבים בקובץ `STEPS.txt` שבתוך ה-zip. באתרים האלה אין ffmpeg, לכן /gif, /opacity ו-/file-format לא עובדים שם. **עדכונים אוטומטיים:** הבוט בודק ב-GitHub כשהוא עולה וכל 3 שעות, מתקין גרסה חדשה (`secrets.env` והתיקייה `data` נשארים) ומפעיל את עצמו מחדש. לכיבוי: `BOTD_AUTO_UPDATE=0` בקובץ `secrets.env`.

**הטלפון כגיבוי (Android):** אפשר להריץ את הבוט בטלפון עם Termux במצב המתנה: הטלפון בודק כל 30 שניות אם הבוט במחשב מחובר, מפעיל
את הבוט כשהמחשב כבוי, ועוצר אותו כשהמחשב חוזר. הצעדים המלאים בחלק האנגלי, תחת "Use your Android phone as a backup".
