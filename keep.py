from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return "IVASMS Bot is Alive and Running!"

def run():
    # Bot-hosting ya cloud platforms ke liye dynamic port zaroori hai
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
