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
            print(f"FloodWait: Sleeping for {e.value} seconds...")
            await asyncio.sleep(e.value + 1)
        except MessageNotModified:
            break
        except Exception as e:
            print(f"Error in operation: {e}")
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

# ==================== COMMANDS ====================

@Client.on_message(
    filters.private &
    (filters.document | filters.video | filters.audio | (filters.text & ~filters.command)) &
    ~filters.command([
        "ssequence", "esequence", "mode", "cancel",
        "add_dump", "rem_dump", "dump_info", "leaderboard"
    ])
)
@check_ban
@check_fsub
async def collect_files(client: Client, message: Message):
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


# ==================== FILE COLLECTOR WITH DEBOUNCE ====================

@Client.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.text & ~filters.command) & ~filters.command(["ssequence", "esequence", "mode", "cancel", "add_dump", "rem_dump", "dump_info", "leaderboard"]))
@check_ban
@check_fsub
async def collect_files(client: Client, message: Message):
    try:
        user_id = message.from_user.id

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

        # Handle text messages (filenames)
        if message.text and not message.text.startswith("/"):
            for line in filter(None, map(str.strip, message.text.splitlines())):
                files.append({'filename': line, 'format': 'text'})
                added_this_time += 1

        # Handle documents
        if message.document:
            files.append({
                'filename': message.document.file_name,
                'format': 'document',
                'file_id': message.document.file_id
            })
            added_this_time += 1

        # Handle videos
        if message.video:
            filename = message.video.file_name if message.video.file_name else (message.caption if message.caption else f"video_{message.video.file_unique_id}.mp4")
            files.append({
                'filename': filename,
                'format': 'video',
                'file_id': message.video.file_id
            })
            added_this_time += 1

        # Handle audio (optional)
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
            await asyncio.sleep(2.3)  # debounce window - adjust if needed (1.8–3.0s)

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
        await handle_floodwait(message.reply_text, "❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ ᴡʜɪʟᴇ ᴘʀᴏᴄᴇssɪɴɢ ғɪʟᴇ.")


# ==================== ESEQUENCE - SEND SORTED FILES ====================

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

        # Clean up pending notification if any
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

        del user_sessions[user_id]

    except Exception as e:
        logger.error(f"Error in esequence command: {e}")
        await handle_floodwait(message.reply_text, f"❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ: {str(e)}")


@Client.on_message(filters.command("cancel") & filters.private)
@check_ban
@check_fsub
async def cancel_cmd(client: Client, message: Message):
    try:
        user_id = message.from_user.id

        if user_id in user_sessions:
            # Cleanup debounce timer if exists
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
                "Usage: <code>/add_dump <Cʜᴀɴɴᴇʟ ɪᴅ></code>",
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
                    "❌ Cannot set a private chat as a dump channel. Use a group/channel ID (negative ID like -100xxxxxxxxxx).",
                    parse_mode=ParseMode.HTML
                )
                return

            try:
                test_msg = await handle_floodwait(
                    client.send_message,
                    chat_id=channel_id,
                    text="✅ Dᴜᴍᴘ ᴄʜᴀɴɴᴇʟ ᴄᴏɴɴᴇᴄᴛᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!"
                )
                await asyncio.sleep(2)
                await test_msg.delete()
            except Exception as e:
                await handle_floodwait(
                    message.reply_text,
                    f"❌ Bᴏᴛ ᴄᴀɴɴᴏᴛ sᴇɴᴅ ᴍᴇssᴀɢᴇs ᴛᴏ ᴛʜɪs ᴄʜᴀɴɴᴇʟ!\n"
                    f"Pʟᴇᴀsᴇ ᴍᴀᴋᴇ ʙᴏᴛ ᴀɴ ᴀᴅᴍɪɴ.\n\n"
                    f"Eʀʀᴏʀ: {str(e)}",
                    parse_mode=ParseMode.HTML
                )
                return

        except Exception as e:
            await handle_floodwait(
                message.reply_text,
                f"❌ Eʀʀᴏʀ: Iɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ ᴏʀ ʙᴏᴛ ɪs ɴᴏᴛ ɪɴ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ.\n\n{str(e)}",
                parse_mode=ParseMode.HTML
            )
            return

        await Seishiro.set_dump_channel(user_id, channel_id)

        await handle_floodwait(
            message.reply_text,
            f"✅ Dᴜᴍᴘ ᴄʜᴀɴɴᴇʟ sᴀᴠᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!\n"
            f"Cʜᴀɴɴᴇʟ: <code>{channel_id}</code>\n\n"
            f"Nᴏᴡ ᴜsᴇ /esequence ᴛᴏ ғᴏʀᴡᴀʀᴅ ғɪʟᴇs ᴛʜᴇʀᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in add_dump command: {e}")
        await handle_floodwait(message.reply_text, f"❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ: {str(e)}", parse_mode=ParseMode.HTML)


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
            f"✅ Dᴜᴍᴘ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ!\n"
            f"Oʟᴅ: <code>{current}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in rem_dump command: {e}")
        await handle_floodwait(message.reply_text, "❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")


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
                "❌ Nᴏ ᴅᴜᴍᴘ ᴄʜᴀɴɴᴇʟ sᴇᴛ.\n\n"
                "Usᴇ /add_dump ᴛᴏ sᴇᴛ ᴏɴᴇ."
            )
        else:
            try:
                chat = await client.get_chat(dump_channel)
                await handle_floodwait(
                    message.reply_text,
                    f"📍 Yᴏᴜʀ Dᴜᴍᴘ Cʜᴀɴɴᴇʟ:\n\n"
                    f"Nᴀᴍᴇ: <b>{chat.title}</b>\n"
                    f"ID: <code>{dump_channel}</code>\n"
                    f"Usᴇʀɴᴀᴍᴇ: @{chat.username if chat.username else 'N/A'}\n\n"
                    f"Usᴇ /rem_dump ᴛᴏ ʀᴇᴍᴏᴠᴇ ɪᴛ.",
                    parse_mode=ParseMode.HTML
                )
            except:
                await handle_floodwait(
                    message.reply_text,
                    f"📍 Yᴏᴜʀ Dᴜᴍᴘ Cʜᴀɴɴᴇʟ:\n\n"
                    f"ID: <code>{dump_channel}</code>\n\n"
                    f"Usᴇ /rem_dump ᴛᴏ ʀᴇᴍᴏᴠᴇ ɪᴛ.",
                    parse_mode=ParseMode.HTML
                )
    except Exception as e:
        logger.error(f"Error in dump_info command: {e}")
        await handle_floodwait(message.reply_text, "❌ Aɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")


@Client.on_message(filters.command("leaderboard") & filters.private)
@check_ban
@check_fsub
async def leaderboard_cmd(client: Client, message: Message):
    """Display top 10 users by sequence count"""
    try:
        user_id = message.from_user.id

        cursor = Seishiro.col.find(
            {"sequence_count": {"$exists": True, "$gt": 0}}
        ).sort("sequence_count", -1).limit(10)

        top_users = await cursor.to_list(length=10)

        if not top_users:
            await handle_floodwait(
                message.reply_text,
                "📊 <b>Sᴇǫᴜᴇɴᴄᴇ Lᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>\n\n"
                "❌ Nᴏ ᴜsᴇʀs ʜᴀᴠᴇ sᴇǫᴜᴇɴᴄᴇᴅ ғɪʟᴇs ʏᴇᴛ!",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return

        leaderboard_text = "📊 <b>Tᴏᴘ 10 Sᴇǫᴜᴇɴᴄᴇ Usᴇʀs</b>\n\n"
        medals = ["1st", "2nd", "3rd"]
        current_user_rank = None
        current_user_count = 0

        for idx, user_data in enumerate(top_users, 1):
            count = user_data.get("sequence_count", 0)
            mention = user_data.get("mention", f"User {user_data['_id']}")

            if user_data["_id"] == user_id:
                current_user_rank = idx
                current_user_count = count

            rank_display = medals[idx - 1] if idx <= 3 else f"{idx}."
            leaderboard_text += f"{rank_display} {mention}\n"
            leaderboard_text += f"   └ <b>{count:,}</b> files sequenced\n\n"

        if current_user_rank is None:
            user_doc = await Seishiro.col.find_one({"_id": user_id})
            user_count = user_doc.get("sequence_count", 0) if user_doc else 0

            if user_count > 0:
                rank = await Seishiro.col.count_documents({
                    "sequence_count": {"$gt": user_count}
                }) + 1
                leaderboard_text += "─────────────────\n"
                leaderboard_text += f"📍 <b>Your Rank:</b> #{rank}\n"
                leaderboard_text += f"   └ <b>{user_count:,}</b> files sequenced"
            else:
                leaderboard_text += "─────────────────\n"
                leaderboard_text += "📍 You haven't sequenced any files yet!"
        else:
            leaderboard_text += "─────────────────\n"
            leaderboard_text += f"🎉 <b>You're ranked #{current_user_rank}!</b>"

        await handle_floodwait(
            message.reply_text,
            leaderboard_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Leaderboard error: {e}", exc_info=True)
        await handle_floodwait(
            message.reply_text,
            "❌ Error loading leaderboard. Try again later."
        )