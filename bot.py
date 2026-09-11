import re
import os
import ssl
import time
import json
import psutil
import asyncio
import platform
import aiohttp
import websockets
import phonenumbers
from aiohttp import web
from datetime import datetime, timedelta, timezone
from phonenumbers import geocoder
from keep import keep_alive 
keep_alive()

from telethon import TelegramClient, events, Button
from telethon.tl.types import (
    ReplyInlineMarkup,
    KeyboardButtonRow,
    KeyboardButtonUrl,
    KeyboardButtonCopy,
)

API_ID = 28822372
API_HASH = "99978f7cdf7bed10f7f35b1a15d85908"
BOT_TOKEN = "8770172989:AAFZwdgxo5Ht-UzESnxFl759kj21TpmdoTQ"

BOT_OWNER_ID = "6067512077"
BOT_OWNER_NAME = "LIL PRINCE"

def is_admin(sender_id):
    if not sender_id:
        return False
    try:
        return int(sender_id) == int(BOT_OWNER_ID)
    except (ValueError, TypeError):
        return str(sender_id).strip() == str(BOT_OWNER_ID).strip()

CONFIG_FILE = "sockets_config.json"
FLEX_CONFIG_FILE = "flex_config.json"

IST = timezone(timedelta(hours=5, minutes=30))

bot = TelegramClient("iva_sms_hosting_bot", API_ID, API_HASH)

start_time = time.time()

active_tasks = {}
connection_status = {}
sockets_config = []

flex_active_tasks = {}
flex_connection_status = {}
flex_config = []

forwarded_flex_cache = {}
user_states = {}

def load_configs():
    global sockets_config, flex_config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                sockets_config = json.load(f)
        except Exception:
            sockets_config = []
            
    if os.path.exists(FLEX_CONFIG_FILE):
        try:
            with open(FLEX_CONFIG_FILE, "r", encoding="utf-8") as f:
                flex_config = json.load(f)
        except Exception:
            flex_config = []

def save_sockets_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(sockets_config, f, indent=2)
    except Exception as e:
        print(f"Error saving {CONFIG_FILE}: {e}")

def save_flex_config():
    try:
        with open(FLEX_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(flex_config, f, indent=2)
    except Exception as e:
        print(f"Error saving {FLEX_CONFIG_FILE}: {e}")

def get_uptime():
    return str(timedelta(seconds=int(time.time() - start_time)))

def mask_number(number: str, last_digits: int = 4, mark: str = "***") -> str:
    if not number:
        return ""
    clean_num = str(number).strip()
    if "*" in clean_num:
        return clean_num if clean_num.startswith("+") else f"+{clean_num}"
    try:
        parsed = phonenumbers.parse("+" + clean_num.lstrip("+"))
        country_code = str(parsed.country_code)
        national_number = str(parsed.national_number)
        if len(national_number) > last_digits:
            masked = mark + national_number[-last_digits:]
        else:
            masked = national_number
        return f"+{country_code}{masked}"
    except Exception:
        if len(clean_num) > last_digits:
            return clean_num[0:3] + mark * (len(clean_num) - (3 + last_digits)) + clean_num[-last_digits:]
        return clean_num

COUNTRY_NAME_TO_REGION = {
    geocoder._region_display_name(reg, "en").lower(): reg
    for cc in range(1, 1000)
    for reg in phonenumbers.region_codes_for_country_code(cc)
    if geocoder._region_display_name(reg, "en")
}

def get_country_and_flag(number: str, raw_country: str = ""):
    country_name = ""
    region_code = None

    if number:
        try:
            clean_num = "+" + str(number).lstrip("+")
            parsed = phonenumbers.parse(clean_num)
            if phonenumbers.is_possible_number(parsed) or phonenumbers.is_valid_number(parsed) or parsed.country_code:
                region_code = phonenumbers.region_code_for_number(parsed)
                if not region_code or region_code == "ZZ":
                    region_code = phonenumbers.region_code_for_country_code(parsed.country_code)
                
                c_name = geocoder.country_name_for_number(parsed, "en") or geocoder.description_for_number(parsed, "en")
                if c_name:
                    country_name = c_name
        except Exception:
            pass

    if not country_name and raw_country and not any(c.isdigit() for c in str(raw_country)):
        country_name = str(raw_country).strip()

    if not region_code and country_name:
        region_code = COUNTRY_NAME_TO_REGION.get(country_name.lower())

    if not country_name:
        if region_code and region_code != "ZZ":
            try:
                country_name = geocoder._region_display_name(region_code, "en")
            except Exception:
                country_name = region_code
        elif raw_country:
            country_name = str(raw_country).strip()
        else:
            country_name = "Unknown"

    flag = ""
    if region_code and len(region_code) == 2 and region_code.isalpha() and region_code.upper() != "ZZ":
        flag = "".join(chr(127397 + ord(c.upper())) for c in region_code)

    return country_name, flag

def format_phone_number(number: str, do_mask: bool) -> str:
    if not number:
        return "N/A"
    clean_num = str(number).strip()
    if do_mask:
        return mask_number(clean_num, last_digits=4)
    else:
        return clean_num if clean_num.startswith("+") else f"+{clean_num}"

def create_inline_buttons(otp_text: str, channel_link: str, chat_link: str):
    rows = []
    if otp_text and otp_text != "N/A":
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCopy(text=f"COPY OTP: {otp_text}", copy_text=otp_text)
        ]))
    
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonUrl(text="CHANNEL", url=channel_link or "https://t.me"),
        KeyboardButtonUrl(text="CHAT", url=chat_link or "https://t.me"),
    ]))
    return ReplyInlineMarkup(rows=rows) if rows else None

async def send_ping(websocket, ping_interval, ping_msg="3"):
    while True:
        await asyncio.sleep(ping_interval / 1000)
        try:
            await websocket.send(ping_msg)
        except Exception:
            break

async def run_socket_listener(config: dict):
    name = config.get("name", "Unknown Socket")
    uri = config.get("uri")
    chat_id = config.get("chat_id")
    topic_id = config.get("topic_id")
    channel_link = config.get("channel_link")
    chat_link = config.get("chat_link")
    
    ssl_context = ssl._create_unverified_context()
    
    while True:
        connection_status[name] = "Disconnected"
        try:
            async with websockets.connect(uri, ssl=ssl_context) as websocket:
                connection_status[name] = "Connected"
                initial_message = await websocket.recv()
                ping_interval = 25000
                try:
                    if initial_message.startswith("0{") and initial_message.endswith("}"):
                        data = json.loads(initial_message[1:])
                        ping_interval = data.get("pingInterval", 25000)
                except Exception:
                    pass

                await websocket.send("40/livesms,")
                asyncio.create_task(send_ping(websocket, ping_interval, "3"))

                while True:
                    message = await websocket.recv()
                    if message.startswith("42/livesms,"):
                        try:
                            json_str = message[message.find("["):]
                            data = json.loads(json_str)
                            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], dict):
                                sms = data[1]
                                raw_number = sms.get("recipient")
                                full_msg = sms.get("message", "") or ""
                                otp_match = re.search(r"\b\d{3,6}(?:[- ]\d{2,6})?", full_msg)
                                otp_code = otp_match.group(0) if otp_match else None
                                
                                country_name, country_flag = get_country_and_flag(raw_number, sms.get("range", ""))
                                country_display = f"<code>{country_name}</code> {country_flag}".strip() if country_flag else f"<code>{country_name}</code>"
                                
                                do_mask = config.get("mask_number", False)
                                formatted_number = format_phone_number(raw_number, do_mask)

                                formatted_msg = (
                                    f"📩 <b>New <u>{sms.get('originator', 'N/A')}</u> OTP Received:</b>\n\n"
                                    f"<blockquote>🛒 <b>Service:</b> <code>{sms.get('originator', 'N/A')}</code></blockquote>\n"
                                    f"<blockquote>📱 <b>Number:</b> <code>{formatted_number}</code></blockquote>\n"
                                    f"<blockquote>🌍 <b>Country:</b> {country_display}</blockquote>\n"
                                    f"<blockquote>🔑 <b>OTP:</b> <code>{otp_code or 'N/A'}</code></blockquote>\n"
                                    f"<blockquote>💬 <b>Full Message:</b>\n<code>{full_msg or 'N/A'}</code></blockquote>"
                                )

                                buttons = create_inline_buttons(otp_code or "N/A", channel_link, chat_link)
                                await bot.send_message(chat_id, formatted_msg, parse_mode="html", reply_to=topic_id, buttons=buttons)
                        except Exception as e:
                            print(f"Error parsing message [{name}]: {e}")
        except Exception as e:
            connection_status[name] = "Disconnected"
            await asyncio.sleep(5)

async def run_flex_api_listener(config: dict):
    name = config.get("name", "Unknown API")
    url = config.get("url")
    chat_id = config.get("chat_id")
    topic_id = config.get("topic_id")
    channel_link = config.get("channel_link")
    chat_link = config.get("chat_link")
    
    if name not in forwarded_flex_cache:
        forwarded_flex_cache[name] = set()

    while True:
        flex_connection_status[name] = "Polling..."
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as response:
                    if response.status == 200:
                        flex_connection_status[name] = "Active"
                        res_json = await response.json()
                        data_list = res_json.get("data", [])
                        
                        if isinstance(data_list, list):
                            for item in data_list:
                                dt = item.get("dt", "")
                                num = item.get("num", "")
                                cli = item.get("cli", "N/A")
                                full_msg = item.get("message", "") or ""
                                
                                unique_key = f"{dt}_{num}_{full_msg}"
                                if unique_key in forwarded_flex_cache[name]:
                                    continue
                                
                                otp_match = re.search(r"\b\d{3,6}(?:[- ]\d{2,6})?", full_msg)
                                otp_code = otp_match.group(0) if otp_match else None
                                
                                country_name, country_flag = get_country_and_flag(num, "")
                                country_display = f"<code>{country_name}</code> {country_flag}".strip() if country_flag else f"<code>{country_name}</code>"
                                
                                do_mask = config.get("mask_number", False)
                                formatted_number = format_phone_number(num, do_mask)

                                formatted_msg = (
                                    f"📩 <b>New <u>{cli}</u> OTP Received:</b>\n\n"
                                    f"<blockquote>🛒 <b>Service:</b> <code>{cli}</code></blockquote>\n"
                                    f"<blockquote>📱 <b>Number:</b> <code>{formatted_number}</code></blockquote>\n"
                                    f"<blockquote>🌍 <b>Country:</b> {country_display}</blockquote>\n"
                                    f"<blockquote>🔑 <b>OTP:</b> <code>{otp_code or 'N/A'}</code></blockquote>\n"
                                    f"<blockquote>💬 <b>Full Message:</b>\n<code>{full_msg or 'N/A'}</code></blockquote>"
                                )

                                buttons = create_inline_buttons(otp_code or "N/A", channel_link, chat_link)
                                await bot.send_message(chat_id, formatted_msg, parse_mode="html", reply_to=topic_id, buttons=buttons)
                                
                                forwarded_flex_cache[name].add(unique_key)
                                if len(forwarded_flex_cache[name]) > 500:
                                    forwarded_flex_cache[name].pop()
                    else:
                        flex_connection_status[name] = f"HTTP Error {response.status}"
        except Exception as e:
            flex_connection_status[name] = "Error"
            print(f"Flex API error [{name}]: {e}")
        
        await asyncio.sleep(10)

def start_socket_task(config: dict):
    name = config.get("name")
    if name in active_tasks and not active_tasks[name].done():
        active_tasks[name].cancel()
    active_tasks[name] = asyncio.create_task(run_socket_listener(config))
    connection_status[name] = "Connecting..."

def start_flex_task(config: dict):
    name = config.get("name")
    if name in flex_active_tasks and not flex_active_tasks[name].done():
        flex_active_tasks[name].cancel()
    flex_active_tasks[name] = asyncio.create_task(run_flex_api_listener(config))
    flex_connection_status[name] = "Starting..."

def build_main_menu_buttons():
    return [
        [Button.inline("IVASMS (WebSocket)", data=b"menu_iva"), Button.inline("FlexSMS (API)", data=b"menu_flex")],
        [Button.inline("Ping Bot", data=b"menu_ping"), Button.inline("Help", data=b"menu_help")]
    ]

def build_iva_menu_buttons():
    return [
        [Button.inline("Add Socket", data=b"iva_add"), Button.inline("Status & Sockets", data=b"iva_status")],
        [Button.inline("Toggle Mask", data=b"iva_toggle_mask"), Button.inline("Delete Socket", data=b"iva_del_socket")],
        [Button.inline("« Main Menu", data=b"menu_main")]
    ]

def build_flex_menu_buttons():
    return [
        [Button.inline("Add API", data=b"flex_add"), Button.inline("Status & APIs", data=b"flex_status")],
        [Button.inline("Toggle Mask", data=b"flex_toggle_mask"), Button.inline("Delete API", data=b"flex_del_socket")],
        [Button.inline("« Main Menu", data=b"menu_main")]
    ]

@bot.on(events.NewMessage(pattern=r'^/(start|help|menu)$'))
async def start_handler(event):
    if not is_admin(event.sender_id):
        await event.reply("⛔ <b>Access Denied!</b>", parse_mode='html')
        return
    msg_text = (
        "⚡ <b>WELCOME TO IVASMS HOSTING BOT</b> ⚡\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <i>Choose your preferred forwarding panel below:</i>\n"
        "• **IVASMS:** Live WebSocket OTP forwarder.\n"
        "• **FlexSMS:** API-based live OTP forwarder with duplicate protection.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await event.reply(msg_text, parse_mode='html', buttons=build_main_menu_buttons())

@bot.on(events.NewMessage(pattern=r'^/ping$'))
async def ping_cmd_handler(event):
    if not is_admin(event.sender_id):
        return
    start = time.time()
    latency = int((time.time() - start) * 1000)
    uptime = get_uptime()
    me = await bot.get_me()
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    msg = (
        f"✦ <b>{me.first_name}</b> is running...\n\n"
        f"✧ <b>Ping</b> ➳ <code>{latency} ms</code>\n"
        f"✧ <b>Up Time</b> ➳ <code>{uptime}</code>\n"
        f"✧ <b>CPU Usage</b> ➳ <code>{cpu}%</code>\n"
        f"✧ <b>RAM Usage</b> ➳ <code>{ram.percent}%</code>"
    )
    await event.reply(msg, parse_mode='html', buttons=[[Button.inline("« Main Menu", data=b"menu_main")]])

@bot.on(events.NewMessage(pattern=r'^(?!/(start|help|menu|ping)$).*'))
async def wizard_messages_handler(event):
    if not is_admin(event.sender_id):
        return
    sender_id = event.sender_id
    if event.text.strip().lower() == '/cancel':
        if sender_id in user_states:
            del user_states[sender_id]
            await event.reply("Setup cancelled.", buttons=build_main_menu_buttons())
        return

    state = user_states.get(sender_id)
    if not state:
        return

    step = state.get("step")
    data = state.get("data", {})
    flow_type = state.get("type")

    if step == "await_name":
        name = event.text.strip()
        if not name:
            await event.reply("Name cannot be empty. Try again:")
            return
        data["name"] = name
        state["step"] = "await_uri"
        
        prompt_txt = "Please reply with the full WebSocket URL:" if flow_type == 'iva' else "Please reply with your API URL (e.g. <code>http://51.77.216.195/...</code>):"
        await event.reply(prompt_txt, parse_mode='html', buttons=[[Button.inline("Cancel Setup", data=b"cancel_wizard")]])

    elif step == "await_uri":
        url = event.text.strip()
        data["uri" if flow_type == 'iva' else "url"] = url
        state["step"] = "await_chat_id"
        await event.reply(
            "Target Chat/Group ID set karein (e.g. <code>-100...</code>):",
            parse_mode='html',
            buttons=[[Button.inline("Cancel Setup", data=b"cancel_wizard")]]
        )

    elif step == "await_chat_id":
        try:
            chat_id = int(event.text.strip())
        except ValueError:
            await event.reply("Invalid ID integer. Try again:")
            return
        data["chat_id"] = chat_id
        state["step"] = "await_channel_link"
        await event.reply("Channel URL bhejein (ya <code>none</code> likhein):", parse_mode='html', buttons=[[Button.inline("Cancel Setup", data=b"cancel_wizard")]]
        )

    elif step == "await_channel_link":
        ch_link = event.text.strip()
        data["channel_link"] = "" if ch_link.lower() == "none" else ch_link
        state["step"] = "await_chat_link"
        await event.reply("Chat URL bhejein (ya <code>none</code> likhein):", parse_mode='html', buttons=[[Button.inline("Cancel Setup", data=b"cancel_wizard")]]
        )

    elif step == "await_chat_link":
        ct_link = event.text.strip()
        data["chat_link"] = "" if ct_link.lower() == "none" else ct_link
        data["mask_number"] = False
        
        if flow_type == 'iva':
            sockets_config.append(data)
            save_sockets_config()
            start_socket_task(data)
        else:
            flex_config.append(data)
            save_flex_config()
            start_flex_task(data)
            
        del user_states[sender_id]
        
        await event.reply(
            f"✅ <b>Successfully Configured!</b>\n\n<b>Name:</b> {data.get('name')}",
            parse_mode='html',
            buttons=build_iva_menu_buttons() if flow_type == 'iva' else build_flex_menu_buttons()
        )

@bot.on(events.CallbackQuery)
async def callback_query_handler(event):
    if not is_admin(event.sender_id):
        await event.answer("Access Denied!", alert=True)
        return
    
    data = event.data
    sender_id = event.sender_id

    if data == b"menu_main":
        if sender_id in user_states:
            del user_states[sender_id]
        msg_text = (
            "⚡ <b>WELCOME TO IVASMS HOSTING BOT</b> ⚡\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🚀 <i>Choose your preferred forwarding panel below:</i>"
        )
        await event.edit(msg_text, parse_mode='html', buttons=build_main_menu_buttons())

    elif data == b"menu_iva":
        await event.edit("<b>IVASMS (WebSocket Panel)</b>", parse_mode='html', buttons=build_iva_menu_buttons())

    elif data == b"menu_flex":
        await event.edit("<b>FlexSMS (API Panel)</b>", parse_mode='html', buttons=build_flex_menu_buttons())

    elif data == b"iva_add":
        user_states[sender_id] = {"step": "await_name", "type": "iva", "data": {}}
        await event.edit("<b>Step 1: Socket Name</b>\nEnter unique name:", parse_mode='html', buttons=[[Button.inline("Cancel", data=b"cancel_wizard")]])

    elif data == b"flex_add":
        user_states[sender_id] = {"step": "await_name", "type": "flex", "data": {}}
        await event.edit("<b>Step 1: API Name</b>\nEnter unique name:", parse_mode='html', buttons=[[Button.inline("Cancel", data=b"cancel_wizard")]])

    elif data == b"iva_status":
        text = f"<b>IVASMS Status ({len(sockets_config)} Total):</b>\n\n"
        for idx, sock in enumerate(sockets_config, 1):
            name = sock.get("name")
            status = connection_status.get(name, "Disconnected")
            text += f"<b>{idx}. {name}</b> - <code>{status}</code>\n"
        if not sockets_config: text = "No Sockets configured."
        await event.edit(text, parse_mode='html', buttons=build_iva_menu_buttons())

    elif data == b"flex_status":
        text = f"<b>FlexSMS Status ({len(flex_config)} Total):</b>\n\n"
        for idx, cfg in enumerate(flex_config, 1):
            name = cfg.get("name")
            status = flex_connection_status.get(name, "Idle")
            text += f"<b>{idx}. {name}</b> - <code>{status}</code>\n"
        if not flex_config: text = "No APIs configured."
        await event.edit(text, parse_mode='html', buttons=build_flex_menu_buttons())

    elif data == b"iva_toggle_mask":
        if not sockets_config:
            await event.answer("No sockets found!", alert=True)
            return
        buttons = [[Button.inline(f"Toggle {s['name']}", data=f"iva_toggle:{i}".encode())] for i, s in enumerate(sockets_config)]
        buttons.append([Button.inline("« Back", data=b"menu_iva")])
        await event.edit("Select socket to toggle mask:", buttons=buttons)

    elif data.startswith(b"iva_toggle:"):
        idx = int(data.decode().split(":")[1])
        sockets_config[idx]["mask_number"] = not sockets_config[idx].get("mask_number", False)
        save_sockets_config()
        await event.answer("Updated!")
        await event.edit("Mask updated successfully!", buttons=build_iva_menu_buttons())

    elif data == b"flex_toggle_mask":
        if not flex_config:
            await event.answer("No APIs found!", alert=True)
            return
        buttons = [[Button.inline(f"Toggle {s['name']}", data=f"flex_toggle:{i}".encode())] for i, s in enumerate(flex_config)]
        buttons.append([Button.inline("« Back", data=b"menu_flex")])
        await event.edit("Select API to toggle mask:", buttons=buttons)

    elif data.startswith(b"flex_toggle:"):
        idx = int(data.decode().split(":")[1])
        flex_config[idx]["mask_number"] = not flex_config[idx].get("mask_number", False)
        save_flex_config()
        await event.answer("Updated!")
        await event.edit("Mask updated successfully!", buttons=build_flex_menu_buttons())

    elif data == b"iva_del_socket":
        if not sockets_config:
            await event.answer("No sockets found!", alert=True)
            return
        buttons = [[Button.inline(f"Delete {s['name']}", data=f"iva_del:{i}".encode())] for i, s in enumerate(sockets_config)]
        buttons.append([Button.inline("« Back", data=b"menu_iva")])
        await event.edit("Select socket to delete:", buttons=buttons)

    elif data.startswith(b"iva_del:"):
        idx = int(data.decode().split(":")[1])
        removed = sockets_config.pop(idx)
        save_sockets_config()
        if removed.get("name") in active_tasks:
            active_tasks[removed.get("name")].cancel()
        await event.answer("Deleted!")
        await event.edit("Deleted successfully!", buttons=build_iva_menu_buttons())

    elif data == b"flex_del_socket":
        if not flex_config:
            await event.answer("No APIs found!", alert=True)
            return
        buttons = [[Button.inline(f"Delete {s['name']}", data=f"flex_del:{i}".encode())] for i, s in enumerate(flex_config)]
        buttons.append([Button.inline("« Back", data=b"menu_flex")])
        await event.edit("Select API to delete:", buttons=buttons)

    elif data.startswith(b"flex_del:"):
        idx = int(data.decode().split(":")[1])
        removed = flex_config.pop(idx)
        save_flex_config()
        if removed.get("name") in flex_active_tasks:
            flex_active_tasks[removed.get("name")].cancel()
        await event.answer("Deleted!")
        await event.edit("Deleted successfully!", buttons=build_flex_menu_buttons())

    elif data == b"menu_ping":
        await ping_cmd_handler(event)

    elif data == b"menu_help":
        await event.edit("📖 Use buttons to manage IVASMS WebSockets or FlexSMS APIs.", buttons=[[Button.inline("« Main Menu", data=b"menu_main")]])

    elif data == b"cancel_wizard":
        if sender_id in user_states:
            del user_states[sender_id]
        await event.edit("Setup cancelled.", buttons=build_main_menu_buttons())

async def main():
    load_configs()
    for sock in sockets_config:
        start_socket_task(sock)
    for cfg in flex_config:
        start_flex_task(cfg)
    
    await bot.start(bot_token=BOT_TOKEN)
    print("IVASMS & FlexSMS Bot is up and running successfully!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
