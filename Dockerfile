# Dashboard only: serves app/ from the committed export in app/data/. No API keys, no network calls.
FROM python:3.12-slim
WORKDIR /srv
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/calib ./src/calib
COPY app ./app
EXPOSE 8501
CMD ["streamlit", "run", "app/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
