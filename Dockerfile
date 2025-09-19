FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get -y upgrade && \
    python3.11 -m pip install --upgrade pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./src .

CMD [ "python", "main.py" ]
