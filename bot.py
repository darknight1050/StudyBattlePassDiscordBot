import discord
from discord import app_commands
from discord.ext import commands

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

POINTS = {
    "page": 3,
    "problem": 10,
    "exam_hour": 80,
}

RANKS = [
    (0, "Bronze"),
    (400, "Silver"),
    (1200, "Gold"),
    (2400, "Platinum"),
    (4000, "Diamond"),
    (5500, "Master"),
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
        exams_done INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0,
        last_activity DATE,
        freezes INTEGER DEFAULT 1
    )
    """)

    conn.commit()
    conn.close()

def ensure_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )

    conn.commit()
    conn.close()

# ------------------ STREAK LOGIC ------------------

def update_streak(user_id):
    today = date.today()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT streak, last_activity, freezes
        FROM users WHERE user_id=?
    """, (user_id,))
    streak, last, freezes = cursor.fetchone()

    if last is None:
        cursor.execute("""
            UPDATE users SET streak=1, last_activity=?
            WHERE user_id=?
        """, (today.isoformat(), user_id))
        conn.commit()
        conn.close()
        return 1

    last = date.fromisoformat(last)

    if today == last:
        conn.close()
        return streak

    if today == last + timedelta(days=1):
        streak += 1
    else:
        if freezes > 0:
            cursor.execute(
                "UPDATE users SET freezes = freezes - 1 WHERE user_id=?",
                (user_id,)
            )
        else:
            streak = 1

    cursor.execute("""
        UPDATE users
        SET streak=?, last_activity=?
        WHERE user_id=?
    """, (streak, today.isoformat(), user_id))

    conn.commit()
    conn.close()
    return streak

# ------------------ RANKS ------------------

def get_rank(points):
    rank = RANKS[0][1]
    for threshold, name in RANKS:
        if points >= threshold:
            rank = name
    return rank

# ------------------ BOT SETUP ------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

# ------------------ SLASH COMMANDS ------------------

@bot.tree.command(name="log", description="Log study activity")
@app_commands.describe(
    activity="page / problem / exam",
    amount="Amount done"
)
async def log(
    interaction: discord.Interaction,
    activity: str,
    amount: int
):
    activity = activity.lower()

    if activity not in ("page", "problem", "exam"):
        await interaction.response.send_message(
            "❌ Activity must be: page, problem, or exam",
            ephemeral=True
        )
        return

    ensure_user(interaction.user.id)
    streak = update_streak(interaction.user.id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if activity == "page":
        points = amount * POINTS["page"]
        cursor.execute("""
            UPDATE users
            SET pages_read = pages_read + ?,
                points = points + ?
            WHERE user_id=?
        """, (amount, points, interaction.user.id))

    elif activity == "problem":
        points = amount * POINTS["problem"]
        cursor.execute("""
            UPDATE users
            SET problems_solved = problems_solved + ?,
                points = points + ?
            WHERE user_id=?
        """, (amount, points, interaction.user.id))

    elif activity == "exam":
        points = amount * POINTS["exam_hour"]
        cursor.execute("""
            UPDATE users
            SET exams_done = exams_done + ?,
                points = points + ?
            WHERE user_id=?
        """, (amount, points, interaction.user.id))

    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"✅ Logged **{amount} {activity}(s)**\n"
        f"⭐ +{points} points\n"
        f"🔥 Streak: {streak}"
    )

# ------------------

@bot.tree.command(name="stats", description="View your stats")
async def stats(interaction: discord.Interaction):
    ensure_user(interaction.user.id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT points, pages_read, problems_solved,
               exams_done, streak, freezes
        FROM users WHERE user_id=?
    """, (interaction.user.id,))

    p, pages, probs, exams, streak, freezes = cursor.fetchone()
    conn.close()

    rank = get_rank(p)

    await interaction.response.send_message(
        f"📊 **{interaction.user.display_name}'s Stats**\n"
        f"🏆 Rank: **{rank}**\n"
        f"⭐ Points: {p}\n"
        f"📖 Pages: {pages}\n"
        f"🧠 Problems: {probs}\n"
        f"📝 Exam hours: {exams}\n"
        f"🔥 Streak: {streak}\n"
        f"❄️ Freezes: {freezes}"
    )

# ------------------

@bot.tree.command(name="leaderboard", description="Top 10 users by points")
async def leaderboard(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT user_id, points
        FROM users
        ORDER BY points DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("No data yet.")
        return

    msg = "🏆 **Leaderboard**\n"
    for i, (uid, pts) in enumerate(rows, start=1):
        user = await bot.fetch_user(uid)
        msg += f"{i}. {user.name} — {pts} pts\n"

    await interaction.response.send_message(msg)

# ------------------ STARTUP ------------------

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
