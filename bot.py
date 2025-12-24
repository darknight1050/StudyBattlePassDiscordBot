import discord
from discord import app_commands
from discord.ext import commands, tasks

import sqlite3
from datetime import date, timedelta
from dotenv import load_dotenv
import os

# ------------------ CONFIG ------------------

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not found in .env file")

DB_FILE = "stats.db"

# ------------------ POINTS CONFIG ------------------
POINTS = {
    "page": 3,           # per page read
    "problem": 10,       # per problem/task
    "exam_hour": 40,     # per hour of exam
}

BONUSES = {
    "problem": {
        "correct_first_try": 1,
        "fixed_mistake": 1,
    },
    "exam": {
        "review": 20,
        "reflection": 10,
        "hard_mode": 10,
    },
    "streak": {
        3: 3,
        5: 5,
    },
}

# ------------------ RANKS ------------------

RANKS = [
    (0, "Bronze"),
    (400, "Silver"),
    (1200, "Gold"),
    (2400, "Platinum"),
    (4000, "Diamond"),
    (5500, "Master"),
]

# ------------------ MILESTONES ------------------

READING_MILESTONES = [
    (50, 10, "📖 Page Turner I"),
    (150, 20, "📖 Page Turner II"),
    (300, 30, "📖 Page Turner III"),
    (600, 50, "📖 Page Turner IV"),
]

PROBLEM_MILESTONES = [
    (25, 15, "🧠 Problem Solver I"),
    (75, 30, "🧠 Problem Solver II"),
    (150, 50, "🧠 Problem Solver III"),
    (300, 75, "🧠 Problem Solver IV"),
]

EXAM_MILESTONES = [
    (10, 25, "🏆 Boss Slayer I"),
    (25, 50, "🏆 Boss Slayer II"),
    (50, 80, "🏆 Boss Slayer III"),
]

# ------------------ DATABASE ------------------

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        points INTEGER DEFAULT 0,
        pages_read INTEGER DEFAULT 0,
        problems_solved INTEGER DEFAULT 0,
        exam_hours INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0,
        last_activity DATE,
        freezes INTEGER DEFAULT 1,
        milestones TEXT DEFAULT '',
        daily_reminder INTEGER DEFAULT 1
    )
    """)
    conn.commit()
    conn.close()

def ensure_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

# ------------------ STREAK LOGIC ------------------

def update_streak(user_id):
    today = date.today()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT streak, last_activity, freezes FROM users WHERE user_id=?", (user_id,))
    streak, last, freezes = cursor.fetchone()

    if last:
        last = date.fromisoformat(last)
        if (today - last).days >= 10:
            cursor.execute("UPDATE users SET freezes=1 WHERE user_id=?", (user_id,))

    if last is None:
        cursor.execute("UPDATE users SET streak=1, last_activity=? WHERE user_id=?", (today.isoformat(), user_id))
        conn.commit()
        conn.close()
        return 1

    if today == last:
        conn.close()
        return streak

    if today == last + timedelta(days=1):
        streak += 1
    else:
        if freezes > 0:
            cursor.execute("UPDATE users SET freezes=freezes-1 WHERE user_id=?", (user_id,))
        else:
            streak = 1

    cursor.execute("UPDATE users SET streak=?, last_activity=? WHERE user_id=?", (streak, today.isoformat(), user_id))
    conn.commit()
    conn.close()
    return streak

def streak_bonus(streak):
    if streak >= 5:
        return BONUSES["streak"][5]
    if streak >= 3:
        return BONUSES["streak"][3]
    return 0

# ------------------ WEEKLY FREEZE RESET ------------------

@tasks.loop(hours=24)
async def weekly_freeze_reset():
    if date.today().weekday() != 0:
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET freezes=1 WHERE freezes<1")
    conn.commit()
    conn.close()

# ------------------ DAILY STREAK REMINDER ------------------

@tasks.loop(hours=24)
async def daily_streak_reminder():
    today = date.today()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, last_activity, streak, daily_reminder FROM users")
    for uid, last, streak, daily_rem in cursor.fetchall():
        if daily_rem == 0 or not last:
            continue
        last = date.fromisoformat(last)
        if today == last or today != last + timedelta(days=1):
            continue
        try:
            user = await bot.fetch_user(uid)
            await user.send(f"🔥 **Streak Reminder**\nYou're on a **{streak}-day streak**! Log something today to keep it alive!")
        except:
            pass
    conn.close()

# ------------------ HELPERS ------------------

def get_rank(points):
    rank = RANKS[0][1]
    for threshold, name in RANKS:
        if points >= threshold:
            rank = name
    return rank

def progress_bar(current, goal, size=10):
    filled = min(size, int(size * current / goal))
    return "█" * filled + "░" * (size - filled)

def check_milestones(cursor, user_id, pages, problems, hours):
    cursor.execute("SELECT milestones FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    claimed = row[0].split(",") if row and row[0] else []
    messages, bonus = [], 0

    def process(milestones, value):
        nonlocal bonus
        for req, reward, name in milestones:
            key = name.replace(" ", "_")
            if value >= req and key not in claimed:
                claimed.append(key)
                bonus += reward
                messages.append(f"🎉 **{name} unlocked!** (+{reward} pts)")

    process(READING_MILESTONES, pages)
    process(PROBLEM_MILESTONES, problems)
    process(EXAM_MILESTONES, hours)

    cursor.execute("UPDATE users SET points=points+?, milestones=? WHERE user_id=?", (bonus, ",".join(claimed), user_id))
    return messages

# ------------------ BOT SETUP ------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    if not weekly_freeze_reset.is_running():
        weekly_freeze_reset.start()
    if not daily_streak_reminder.is_running():
        daily_streak_reminder.start()
    print(f"✅ Logged in as {bot.user}")

# ------------------ /LOG ------------------

@bot.tree.command(name="log", description="Log study activity")
@app_commands.describe(
    activity="page / problem / exam",
    amount="Pages, problems, or exam hours",
    correct_first_try=f"Problems only: +{BONUSES['problem']['correct_first_try']} per correct first try",
    fixed_mistake=f"Problems only: +{BONUSES['problem']['fixed_mistake']} per fixed mistake",
    review=f"Exams only: +{BONUSES['exam']['review']} for review & corrections",
    reflection=f"Exams only: +{BONUSES['exam']['reflection']} for reflection notes",
    hard_mode=f"Exams only: +{BONUSES['exam']['hard_mode']} for hard mode"
)
async def log(
    interaction: discord.Interaction,
    activity: str,
    amount: int,
    correct_first_try: int = 0,
    fixed_mistake: int = 0,
    review: int = 0,
    reflection: int = 0,
    hard_mode: int = 0
):
    activity = activity.lower()
    ensure_user(interaction.user.id)
    streak = update_streak(interaction.user.id)
    streak_pts = streak_bonus(streak)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    total_bonus = 0
    if activity == "page":
        points = amount * POINTS["page"] + streak_pts
        total_bonus += streak_pts
        cursor.execute("UPDATE users SET pages_read=pages_read+?, points=points+? WHERE user_id=?", (amount, points, interaction.user.id))
    elif activity == "problem":
        bonus_points = (correct_first_try * BONUSES["problem"]["correct_first_try"] +
                        fixed_mistake * BONUSES["problem"]["fixed_mistake"])
        points = amount * POINTS["problem"] + bonus_points + streak_pts
        total_bonus += bonus_points + streak_pts
        cursor.execute("UPDATE users SET problems_solved=problems_solved+?, points=points+? WHERE user_id=?", (amount, points, interaction.user.id))
    elif activity == "exam":
        bonus_points = (review * BONUSES["exam"]["review"] +
                        reflection * BONUSES["exam"]["reflection"] +
                        hard_mode * BONUSES["exam"]["hard_mode"])
        points = amount * POINTS["exam_hour"] + bonus_points + streak_pts
        total_bonus += bonus_points + streak_pts
        cursor.execute("UPDATE users SET exam_hours=exam_hours+?, points=points+? WHERE user_id=?", (amount, points, interaction.user.id))
    else:
        await interaction.response.send_message("❌ Invalid activity", ephemeral=True)
        return

    cursor.execute("SELECT pages_read, problems_solved, exam_hours FROM users WHERE user_id=?", (interaction.user.id,))
    pages, probs, hours = cursor.fetchone()
    milestone_msgs = check_milestones(cursor, interaction.user.id, pages, probs, hours)

    conn.commit()
    conn.close()

    embed = discord.Embed(title=f"📌 Activity Logged: {activity.capitalize()}", color=discord.Color.blue())
    embed.add_field(name="Amount", value=str(amount), inline=True)
    embed.add_field(name="Base Points", value=str(amount * POINTS.get(f"{activity if activity != 'exam' else 'exam_hour'}", 0)), inline=True)
    embed.add_field(name="Bonus Points", value=str(total_bonus), inline=True)
    embed.add_field(name="Streak", value=f"{streak} (+{streak_pts})", inline=True)
    embed.add_field(name="Total Points Gained", value=str(points), inline=False)
    if milestone_msgs:
        embed.add_field(name="🎉 Milestones Unlocked", value="\n".join(milestone_msgs), inline=False)

    await interaction.response.send_message(embed=embed)

# ------------------ /DAILY ------------------

@bot.tree.command(name="daily", description="View today’s summary & streak status")
async def daily(interaction: discord.Interaction):
    ensure_user(interaction.user.id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT streak, freezes, last_activity FROM users WHERE user_id=?", (interaction.user.id,))
    streak, freezes, last = cursor.fetchone()
    conn.close()

    today = date.today()
    logged_today = last == today.isoformat()

    embed = discord.Embed(title="📅 Daily Summary", color=discord.Color.blue())
    embed.add_field(name="🔥 Streak", value=f"{streak} days (+{streak_bonus(streak)}/log)", inline=False)
    embed.add_field(name="❄️ Freezes", value=str(freezes), inline=True)
    embed.add_field(name="📌 Status", value="✅ Logged today" if logged_today else "⚠️ Not logged yet", inline=True)
    if not logged_today:
        embed.set_footer(text="Log something today to protect your streak!")

    await interaction.response.send_message(embed=embed)

# ------------------ /STATS ------------------

@bot.tree.command(name="stats", description="View your stats")
async def stats(interaction: discord.Interaction):
    ensure_user(interaction.user.id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT points, pages_read, problems_solved, exam_hours, streak, freezes
        FROM users WHERE user_id=?
    """, (interaction.user.id,))
    p, pages, probs, exams, streak, freezes = cursor.fetchone()
    conn.close()

    rank = get_rank(p)

    embed = discord.Embed(title=f"📊 {interaction.user.display_name}'s Stats", color=discord.Color.green())
    embed.add_field(name="🏆 Rank", value=rank, inline=False)
    embed.add_field(name="⭐ Points", value=p, inline=True)
    embed.add_field(name="📖 Pages Read", value=pages, inline=True)
    embed.add_field(name="🧠 Problems Solved", value=probs, inline=True)
    embed.add_field(name="📝 Exam Hours", value=exams, inline=True)
    embed.add_field(name="🔥 Streak", value=streak, inline=True)
    embed.add_field(name="❄️ Freezes", value=freezes, inline=True)

    await interaction.response.send_message(embed=embed)

# ------------------ /MILESTONES ------------------

@bot.tree.command(name="milestones", description="View your milestone progress")
async def milestones(interaction: discord.Interaction):
    ensure_user(interaction.user.id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT pages_read, problems_solved, exam_hours, milestones FROM users WHERE user_id=?", (interaction.user.id,))
    pages, probs, hours, claimed_raw = cursor.fetchone()
    conn.close()

    claimed = claimed_raw.split(",") if claimed_raw else []
    embed = discord.Embed(title="🏆 Milestone Progress", color=discord.Color.gold())

    def section(title, value, milestones):
        lines = []
        for req, reward, name in milestones:
            key = name.replace(" ", "_")
            done = key in claimed
            status = "✅" if done else "🔒"
            bar = progress_bar(value, req)
            lines.append(f"{status} **{name}**\n`{bar}` {min(value, req)}/{req} (+{reward} pts)")
        embed.add_field(name=title, value="\n\n".join(lines), inline=False)

    section("📖 Reading", pages, READING_MILESTONES)
    section("🧠 Problems", probs, PROBLEM_MILESTONES)
    section("🏆 Exams", hours, EXAM_MILESTONES)

    await interaction.response.send_message(embed=embed)

# ------------------ /REMINDER ------------------

@bot.tree.command(name="reminder", description="Enable or disable daily streak reminders")
@app_commands.describe(option="on / off")
async def reminder(interaction: discord.Interaction, option: str):
    ensure_user(interaction.user.id)
    option = option.lower()
    if option not in ("on", "off"):
        await interaction.response.send_message("❌ Option must be 'on' or 'off'", ephemeral=True)
        return
    value = 1 if option == "on" else 0
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET daily_reminder=? WHERE user_id=?", (value, interaction.user.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Daily reminder turned **{option.upper()}**")

# ------------------ START ------------------

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
