FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY market_service.py .
COPY agent.py .
COPY static/ ./static/

ENV PORT=8080
ENV AUDIT_LOG=/data/audit.jsonl

EXPOSE 8080

CMD ["python", "market_service.py"]
