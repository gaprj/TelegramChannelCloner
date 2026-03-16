import asyncio
import os
import configparser
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

BASE_DIR = os.getcwd()
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

config = configparser.ConfigParser()
config.read(CONFIG_FILE)

API_ID = int(config['API']['ID'])
API_HASH = config['API']['HASH']
SESSION_FILE = 'cloner_session_qr'

async def do_qr_login(client):
    print("\n--- QR CODE LOGIN ---")
    qr_login = await client.qr_login()
    print(f"\nOpen this link on Telegram (Settings -> Devices):\n{qr_login.url}\n")
    try:
        import qrcode, io
        qr = qrcode.QRCode(version=1, border=2)
        qr.add_data(qr_login.url)
        qr.make(fit=True)
        f = io.StringIO()
        qr.print_ascii(out=f, invert=True)
        print(f.getvalue())
    except ImportError:
        print("Tip: pip install qrcode for a visual QR code.")
    
    print("Waiting for scanner...")
    try:
        await qr_login.wait()
    except SessionPasswordNeededError:
        pw = input("\n2FA Password: ")
        await client.sign_in(password=pw)
    print("Login successful!\n")

async def main():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("\n--- LOGIN REQUIRED ---")
        print("1. Login via QR Code (Recommended)")
        print("2. Login via Phone Number")
        choice = input("Select an option (1 or 2): ").strip()
        
        if choice == '1':
            await do_qr_login(client)
        else:
            print("\n--- PHONE NUMBER LOGIN ---")
            await client.start()
    
    print("\n--- YOUR CHATS AND CHANNELS LIST ---")
    async for dialog in client.iter_dialogs():
        print(f"Name: {dialog.name} | ID: {dialog.id}")
    await client.disconnect()
    print("------------------------------------\n")

if __name__ == '__main__':
    asyncio.run(main())