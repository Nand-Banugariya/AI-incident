FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY demo_api ./demo_api
EXPOSE 8080
CMD ["uvicorn", "demo_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
