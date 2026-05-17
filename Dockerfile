FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cf_xray_dns_sync.py .

CMD ["python", "cf_xray_dns_sync.py"]