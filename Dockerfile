FROM python:3.11

RUN apt-get update && apt-get -y upgrade

RUN python3.11 -m pip install --upgrade pip

WORKDIR /app
COPY ./requirements.txt /app
RUN pip install -r ./requirements.txt

COPY ./src /app

CMD [ "python", "/app/main.py" ]
