import re
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.enums import ParseMode
from datetime import datetime

from config import *
from Plugins.callbacks import *
from Database.database import Seishiro
from Plugins.start import *

logger = logging.getLogger(__name__)

user_sessions = {}
pending_notifications = {}  # user_id → {'timer': asyncio.Task, 'last_count': int}

# ==================== FLOODWAIT HANDLER ====================

async def handle_floodwait(func, *args, **kwargs):
    """Generic FloodWait handler for any Pyrogram method"""
    while True:
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            logger.warning(f"FloodWait: Sleeping for {e.value} seconds...")
            await asyncio.sleep(e.value + 1)
        except MessageNotModified:
            break
        except Exception as e:
            logger.error(f"Error in operation: {e}")
            break

# ==================== FILE PARSING & SORTING ====================

def extract_file_info(filename, file_format, file_id=None):
    quality_match = re.search(QUALITY_PATTERN, filename, re.IGNORECASE)
    quality = quality_match.group(1).lower() if quality_match else 'unknown'

    temp = re.sub(QUALITY_PATTERN, '', filename, flags=re.IGNORECASE) if quality_match else filename

    season_match = re.search(SEASON_PATTERN, temp)
    season = int(season_match.group(1)) if season_match else 0

    episode_match = re.search(EPISODE_PATTERN, temp)
    episode = int(episode_match.group(1)) if episode_match else 0
    if not episode_match:
        nums = re.findall(r'\d{1,3}', temp)
        episode = int(nums[-1]) if nums else 0

    return {
        'filename': filename,
        'format': file_format,
        'file_id': file_id,
        'season': season,
        'episode': episode,
        'quality': quality,
        'quality_order': QUALITY_ORDER.get(quality, 7),
        'is_series': bool(season or episode)
    }


def parse_and_sort_files(file_data, mode='All'):
    """
    Supported modes:
    • Quality     → quality only
    • Season      → season only
    • Episode     → episode only
    • All         → Season → Episode → Quality     (classic)
    • AllSQE      → Season → Quality → Episode     (new!)
    """
    series, non_series = [], []

    for item in file_data:
        info = extract_file_info(item['filename'], item['format'], item.get('file_id'))
        (series if info['is_series'] else non_series).append(info)

    if mode == 'Quality':
        series = sorted(series, key=lambda x: (x['quality_order'], x['filename'].lower()))
    elif mode == 'Season':
        series = sorted(series, key=lambda x: (x['season'], x['filename'].lower()))
    elif mode == 'Episode':
        series = sorted(series, key=lambda x: (x['episode'], x['filename'].lower()))
    elif mode == 'AllSQE':
        series = sorted(series, key=lambda x: (x['season'], x['quality_order'], x['episode']))
    else:  # 'All' - default/classic
        series = sorted(series, key=lambda x: (x['season'], x['episode'], x['quality_order']))

    non_series = sorted(non_series, key=lambda x: (x['filename'].lower(), x['quality_order']))

    return series, non_series

# ==================== EXCLUDED COMMANDS ====================

EXCLUDED_COMMANDS = [
    "ssequence", "esequence", "mode", "cancel",
    "add_dump", "rem_dump", "dump_info", "leaderboard"
]

# ==================== FILE COLLECTOR ====================

@Client.on_message(
    filters.private &
    (filters.document | filters.video | filters.audio | filters.text) &
    ~filters.command(EXCLUDED_COMMANDS)
)
@check_ban
@check_fsub
async def collect_files(client: Client, message: Message):
    try:
        user_id = message.from_user.id

        # If no active sequence session
        if user_id not in user_sessions:
            if message.document or message.video or message.audio:
                await handle_floodwait(
                    message.reply_text,
                    "Usᴇ /ssequence ғɪʀsᴛ ᴛʜᴇɴ sᴇɴᴅ ᴛʜᴇ ғɪʟᴇ(s)."
                )
            return

        session = user_sessions[user_id]
        files = session['files']
        added_this_time = 0

        # Text as filenames
        if message.text and not message.text.startswith("/"):
            for line in filter(None, map(str.strip, message.text.splitlines())):
                files.append({'filename': line, 'format': 'text'})
                added_this_time += 1

        # Documents
        if message.document:
            files.append({
                'filename': message.document.file_name,
                'format': 'document',
                'file_id': message.document.file_id
            })
            added_this_time += 1

        # Videos
        if message.video:
            filename = message.video.file_name or \
                       (message.caption if message.caption else f"video_{message.video.file_unique_id}.mp4")
            files.append({
                'filename': filename,
                'format': 'video',
                'file_id': message.video.file_id
            })
            added_this_time += 1

        # Audio (optional - remove if not needed)
        if message.audio:
            filename = message.audio.file_name or f"audio_{message.audio.file_unique_id}"
            files.append({
                'filename': filename,
                'format': 'audio',
                'file_id': message.audio.file_id
            })
            added_this_time += 1

        if added_this_time == 0:
            return

        current_total = len(files)

        # ─── DEBOUNCE LOGIC ───────────────────────────────────────
        if user_id in pending_notifications:
            old_task = pending_notifications[user_id].get('timer')
            if old_task and not old_task.done():
                old_task.cancel()

        async def send_debounced_notification():
            await asyncio.sleep(2.3)  # debounce window

            if user_id in user_sessions and len(user_sessions[user_id]['files']) == current_total:
                current_mode = session.get('mode', 'All')
                mode_display = {
                    'Quality': 'Quality only',
                    'All': 'All (S→E→Q)',
                    'AllSQE': 'All [S→Q→E]',
                    'Episode': 'Episode only',
                    'Season': 'Season only'
                }.get(current_mode, current_mode)

                text = (
                    f"✅ <b>{added_this_time} file(s) added to sequence</b>\n"
                    f"Total files: <code>{current_total}</code>\n\n"
                    f"Current mode: <b>{mode_display}</b>\n"
                    f"Use <code>/esequence</code> when you're done"
                )

                await handle_floodwait(
                    message.reply_text,
                    text,
                    parse_mode=ParseMode.HTML
                )

            pending_notifications.pop(user_id, None)

        pending_notifications[user_id] = {
            'timer': asyncio.create_task(send_debounced_notification()),
            'last_count': current_total
        }

    except Exception as e:
        logger.error(f"Error in collect_files: {e}")
        await handle_floodwait(message.reply_text, "❌ An error occurred while processing file.")


# ==================== START SEQUENCE ====================

@Client.on_message(filters.command("ssequence") & filters.private)
@check_ban
@check_fsub
async def arrange_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        user_sessions[user_id] = {'files': [], 'mode': 'All'}

        await handle_floodwait(
            message.reply_text,
            "<b><i>Sᴇǫᴜᴇɴᴄᴇ sᴛᴀʀᴛᴇᴅ</i></b>\n\n"
            "<i>Nᴏᴡ sᴇɴᴅ ʏᴏᴜʀ ғɪʟᴇ(s) ғᴏʀ sᴇǫᴜᴇɴᴄᴇ.</i>\n"
            "• Usᴇ /mode ᴛᴏ ᴄʜᴀɴɢᴇ ᴛʜᴇ ᴍᴏᴅᴇ ᴏғ sᴇǫᴜᴇɴᴄɪɴɢ."
        )
    except Exception as e:
        logger.error(f"Error in ssequence command: {e}")
        await handle_floodwait(message.reply_text, "❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")


# ==================== MODE COMMAND ====================

@Client.on_message(filters.command("mode") & filters.private)
@check_ban
@check_fsub
async def mode_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        current = await Seishiro.get_sequence_mode(user_id) or "All"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Qᴜᴀʟɪᴛʏ{' ✅' if current == 'Quality' else ''}", callback_data="mode_Quality"),
             InlineKeyboardButton(f"Aʟʟ (S→E→Q){' ✅' if current == 'All' else ''}", callback_data="mode_All")],
            [InlineKeyboardButton(f"Aʟʟ [S→Q→E]{' ✅' if current == 'AllSQE' else ''}", callback_data="mode_AllSQE"),
             InlineKeyboardButton(f"Eᴘɪsᴏᴅᴇ{' ✅' if current == 'Episode' else ''}", callback_data="mode_Episode")],
            [InlineKeyboardButton(f"Sᴇᴀsᴏɴ{' ✅' if current == 'Season' else ''}", callback_data="mode_Season")]
        ])

        await handle_floodwait(
            message.reply_text,
            f"<b><u>Sᴇʟᴇᴄᴛ Sᴏʀᴛɪɴɢ Mᴏᴅᴇ</u></b> (Current: {current})\n\n"
            "<b>Available modes:</b>\n"
            "• <b>Qᴜᴀʟɪᴛʏ</b>: Sort by quality only\n"
            "• <b>Aʟʟ (S→E→Q)</b>: Season → Episode → Quality\n"
            "• <b>Aʟʟ [S→Q→E]</b>: Season → Quality → Episode\n"
            "• <b>Eᴘɪsᴏᴅᴇ</b>: Sort by episode number only\n"
            "• <b>Sᴇᴀsᴏɴ</b>: Sort by season number only\n\n"
            "<i>Choose your preferred order ↓</i>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in mode command: {e}")
        await handle_floodwait(message.reply_text, "❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")


# ==================== ESEQUENCE ====================

@Client.on_message(filters.command("esequence") & filters.private)
@check_ban
@check_fsub
async def end_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        session = user_sessions.get(user_id)

        if not session or not session['files']:
            await handle_floodwait(message.reply_text, "Nᴏ ғɪʟᴇs ᴡᴇʀᴇ sᴇɴᴛ ғᴏʀ sᴇǫᴜᴇɴᴄᴇ")
            return

        # Cleanup pending notification
        if user_id in pending_notifications:
            task = pending_notifications[user_id].get('timer')
            if task and not task.done():
                task.cancel()
            pending_notifications.pop(user_id, None)

        dump_channel = await Seishiro.get_dump_channel(user_id)

        series, non_series = parse_and_sort_files(session['files'], session.get('mode', 'All'))
        total_files = len(series) + len(non_series)
        all_sorted_files = series + non_series

        is_dump_mode = bool(dump_channel)

        if is_dump_mode:
            await handle_floodwait(
                message.reply_text,
                f"📤 Sᴇɴᴅɪɴɢ {total_files} ғɪʟᴇs ᴛᴏ ʏᴏᴜʀ ᴅᴜᴍᴘ ᴄʜᴀɴɴᴇʟ...\n"
                f"Cʜᴀɴɴᴇʟ: <code>{dump_channel}</code>",
                parse_mode=ParseMode.HTML
            )
            target_chat = dump_channel
        else:
            await handle_floodwait(
                message.reply_text,
                f"📤 Sᴇɴᴅɪɴɢ {total_files} ғɪʟᴇs ɪɴ sᴇǫᴜᴇɴᴄᴇ ᴛᴏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ...",
                parse_mode=ParseMode.HTML
            )
            target_chat = message.chat.id

        sent_count = 0
        failed_files = []

        try:
            for file_info in all_sorted_files:
                try:
                    file_id = file_info.get('file_id')
                    filename = file_info.get('filename', 'Unknown')
                    file_format = file_info.get('format')

                    if file_id and file_format in ['document', 'video', 'audio']:
                        if file_format == 'document':
                            await handle_floodwait(
                                client.send_document,
                                chat_id=target_chat,
                                document=file_id,
                                caption=filename
                            )
                        elif file_format == 'video':
                            await handle_floodwait(
                                client.send_video,
                                chat_id=target_chat,
                                video=file_id,
                                caption=filename
                            )
                        elif file_format == 'audio':
                            await handle_floodwait(
                                client.send_audio,
                                chat_id=target_chat,
                                audio=file_id,
                                caption=filename
                            )
                    else:
                        await handle_floodwait(
                            client.send_message,
                            chat_id=target_chat,
                            text=f"📄 {filename}"
                        )

                    sent_count += 1

                except Exception as file_error:
                    logger.error(f"Failed to send file {filename}: {file_error}")
                    failed_files.append(filename)
                    continue

            completion_msg = f"✅ Sᴜᴄᴄᴇssғᴜʟʟʏ sᴇɴᴛ {sent_count}/{total_files} ғɪʟᴇs ɪɴ sᴇǫᴜᴇɴᴄᴇ"

            if is_dump_mode:
                completion_msg += " ᴛᴏ ʏᴏᴜʀ ᴅᴜᴍᴘ ᴄʜᴀɴɴᴇʟ!"
            else:
                completion_msg += "!"

            if failed_files:
                completion_msg += f"\n\n⚠️ Fᴀɪʟᴇᴅ: {len(failed_files)} ғɪʟᴇs"
                if len(failed_files) <= 5:
                    completion_msg += "\n" + "\n".join([f"• {f}" for f in failed_files])

            await handle_floodwait(message.reply_text, completion_msg)

        except Exception as send_error:
            logger.error(f"Error during file sending: {send_error}")

            if is_dump_mode:
                await handle_floodwait(
                    message.reply_text,
                    f"❌ Eʀʀᴏʀ sᴇɴᴅɪɴɢ ᴛᴏ ᴅᴜᴍᴘ ᴄʜᴀɴɴᴇʟ!\n"
                    f"Mᴀᴋᴇ sᴜʀᴇ ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ.\n\n"
                    f"Sᴇɴᴅɪɴɢ ᴛᴏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ ɪɴsᴛᴇᴀᴅ..."
                )

                sent_count = 0
                for file_info in all_sorted_files:
                    try:
                        file_id = file_info.get('file_id')
                        filename = file_info.get('filename', 'Unknown')
                        file_format = file_info.get('format')

                        if file_id and file_format in ['document', 'video', 'audio']:
                            if file_format == 'document':
                                await handle_floodwait(
                                    client.send_document,
                                    chat_id=message.chat.id,
                                    document=file_id,
                                    caption=filename
                                )
                            elif file_format == 'video':
                                await handle_floodwait(
                                    client.send_video,
                                    chat_id=message.chat.id,
                                    video=file_id,
                                    caption=filename
                                )
                            elif file_format == 'audio':
                                await handle_floodwait(
                                    client.send_audio,
                                    chat_id=message.chat.id,
                                    audio=file_id,
                                    caption=filename
                                )
                        else:
                            await handle_floodwait(
                                client.send_message,
                                chat_id=message.chat.id,
                                text=f"📄 {filename}"
                            )

                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Failed to send file in fallback: {e}")
                        continue

                await handle_floodwait(
                    message.reply_text,
                    f"✅ Sᴇɴᴛ {sent_count}/{total_files} ғɪʟᴇs ᴛᴏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ!"
                )
            else:
                raise send_error

        await Seishiro.col.update_one(
            {"_id": int(user_id)},
            {
                "$inc": {"sequence_count": sent_count},
                "$set": {
                    "mention": message.from_user.mention,
                    "last_activity_timestamp": datetime.now()
                }
            }
        )

        if user_id in user_sessions:
            del user_sessions[user_id]

    except Exception as e:
        logger.error(f"Error in esequence command: {e}")
        await handle_floodwait(message.reply_text, f"❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ: {str(e)}")


# ==================== CANCEL ====================

@Client.on_message(filters.command("cancel") & filters.private)
@check_ban
@check_fsub
async def cancel_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id

        if user_id in user_sessions:
            if user_id in pending_notifications:
                task = pending_notifications[user_id].get('timer')
                if task and not task.done():
                    task.cancel()
                pending_notifications.pop(user_id, None)

            if user_sessions[user_id].get('status_msg'):
                try:
                    await user_sessions[user_id]['status_msg'].delete()
                except:
                    pass

            del user_sessions[user_id]
            await handle_floodwait(message.reply_text, "Sᴇǫᴜᴇɴᴄᴇ ᴄᴀɴᴄᴇʟʟᴇᴅ...!!")
        else:
            await handle_floodwait(message.reply_text, "Nᴏ ᴀᴄᴛɪᴠᴇ sᴇǫᴜᴇɴᴄᴇ ғᴏᴜɴᴅ.")
    except Exception as e:
        logger.error(f"Error in cancel command: {e}")
        await handle_floodwait(message.reply_text, "❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")


# ==================== DUMP CHANNEL COMMANDS ====================

@Client.on_message(filters.command("add_dump") & filters.private)
@check_ban
@check_fsub
async def add_dump_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id

        from time import time
        if not hasattr(add_dump_cmd, 'user_cooldowns'):
            add_dump_cmd.user_cooldowns = {}
        if user_id in add_dump_cmd.user_cooldowns and (time() - add_dump_cmd.user_cooldowns[user_id]) < 5:
            return
        add_dump_cmd.user_cooldowns[user_id] = time()

        if len(message.command) < 2:
            await handle_floodwait(
                message.reply_text,
                "Usage: <code>/add_dump <Channel ID></code>",
                parse_mode=ParseMode.HTML
            )
            return

        target = message.command[1]
        try:
            if target.startswith("-100") or target.startswith("-"):
                channel_id = int(target)
            else:
                if target.startswith("@"):
                    target = target[1:]
                entity = await client.get_chat(target)
                channel_id = entity.id

            if channel_id > 0:
                await handle_floodwait(
                    message.reply_text,
                    "❌ Cannot set a private chat as dump channel. Use negative ID.",
                    parse_mode=ParseMode.HTML
                )
                return

            test_msg = await handle_floodwait(
                client.send_message,
                chat_id=channel_id,
                text="✅ Dump channel connected successfully!"
            )
     
except Exception as e:
        logger.error(f"Error in add_dump: {e}")
        await handle_floodwait(message.reply_text, f"❌ Error: {str(e)}", parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("rem_dump") & filters.private)
@check_ban
@check_fsub
async def rem_dump_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        current = await Seishiro.get_dump_channel(user_id)

        if not current:
            await handle_floodwait(message.reply_text, "Yᴏᴜ ʜᴀᴠᴇɴ'ᴛ sᴇᴛ ᴀɴʏ ᴅᴜᴍᴘ ᴄʜᴀɴɴᴇʟ ʏᴇᴛ.")
            return

        await Seishiro.remove_dump_channel(user_id)
        await handle_floodwait(
            message.reply_text,
            f"✅ Dump channel removed!\nOld ID: <code>{current}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in rem_dump: {e}")
        await handle_floodwait(message.reply_text, "❌ An error occurred.", parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("dump_info") & filters.private)
@check_ban
@check_fsub
async def dump_info_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        dump_channel = await Seishiro.get_dump_channel(user_id)

        if not dump_channel:
            await handle_floodwait(
                message.reply_text,
                "❌ No dump channel set.\nUse /add_dump to set one."
            )
            return

        try:
            chat = await client.get_chat(dump_channel)
            await handle_floodwait(
                message.reply_text,
                f"📍 **Your Dump Channel:**\n\n"
                f"Name: <b>{chat.title}</b>\n"
                f"ID: <code>{dump_channel}</code>\n"
                f"Username: @{chat.username if chat.username else 'N/A'}\n\n"
                f"Use /rem_dump to remove.",
                parse_mode=ParseMode.HTML
            )
        except:
            await handle_floodwait(
                message.reply_text,
                f"📍 **Your Dump Channel:**\n\n"
                f"ID: <code>{dump_channel}</code>\n\n"
                f"Use /rem_dump to remove.\n(Couldn't fetch title)",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Error in dump_info: {e}")
        await handle_floodwait(message.reply_text, "❌ An error occurred.", parse_mode=ParseMode.HTML)


# ==================== LEADERBOARD ====================

@Client.on_message(filters.command("leaderboard") & filters.private)
@check_ban
@check_fsub
async def leaderboard_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id

        cursor = Seishiro.col.find(
            {"sequence_count": {"$exists": True, "$gt": 0}}
        ).sort("sequence_count", -1).limit(10)

        top_users = await cursor.to_list(length=10)

        if not top_users:
            await handle_floodwait(
                message.reply_text,
                "📊 <b>Sequence Leaderboard</b>\n\n"
                "❌ No users have sequenced files yet!",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return

        text = "📊 <b>Top 10 Sequence Users</b>\n\n"
        medals = ["🥇", "🥈", "🥉"]

        current_user_rank = None
        current_user_count = 0

        for idx, user in enumerate(top_users, 1):
            count = user.get("sequence_count", 0)
            mention = user.get("mention", f"User {user['_id']}")

            if user["_id"] == user_id:
                current_user_rank = idx
                current_user_count = count

            rank = medals[idx-1] if idx <= 3 else f"{idx}."
            text += f"{rank} {mention}\n"
            text += f"   └ <b>{count:,}</b> files sequenced\n\n"

        if current_user_rank is None:
            user_doc = await Seishiro.col.find_one({"_id": user_id})
            user_count = user_doc.get("sequence_count", 0) if user_doc else 0

            if user_count > 0:
                rank = await Seishiro.col.count_documents({
                    "sequence_count": {"$gt": user_count}
                }) + 1
                text += "─────────────────\n"
                text += f"📍 <b>Your Rank:</b> #{rank}\n"
                text += f"   └ <b>{user_count:,}</b> files sequenced"
            else:
                text += "─────────────────\n"
                text += "📍 You haven't sequenced any files yet!"
        else:
            text += "─────────────────\n"
            text += f"🎉 <b>You're ranked #{current_user_rank}!</b>"

        await handle_floodwait(
            message.reply_text,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Leaderboard error: {e}", exc_info=True)
        await handle_floodwait(
            message.reply_text,
            "❌ Error loading leaderboard. Try again later."
        )