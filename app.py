import os
from flask import Flask, render_template

app = Flask(__name__)

@app.get("/")
def home():
    return render_template("index.html")

if __name__ == "__main__":
    # lokálisan oké, Renderen ezt úgyis gunicorn indítja
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
