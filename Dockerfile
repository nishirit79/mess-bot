FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    libfreetype6-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# ডেটাবেস ফোল্ডার তৈরি
RUN mkdir -p /app/data && chmod 777 /app/data

# ফন্ট ফোল্ডার কপি করুন
COPY fonts/ /app/fonts/

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
