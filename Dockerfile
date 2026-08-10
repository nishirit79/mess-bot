FROM python:3.11-slim

WORKDIR /app

# PDF রিপোর্টের জন্য প্রয়োজনীয় প্যাকেজ
RUN apt-get update && apt-get install -y \
    build-essential \
    libfreetype6-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
