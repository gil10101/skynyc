# Shared image for the three ingestion services (producer-opensky,
# producer-weather, consumer-weather). One build, three commands — the services
# differ only in their compose `command:`. Python pinned per the stack (3.11).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY producers/ producers/
COPY consumers/ consumers/

# No default command: compose supplies `python -m <module>` per service.
