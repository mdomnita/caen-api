FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1

WORKDIR /app

# Install python modules
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Initial seeding files
COPY temp/caen_rev3_coduri_clase.csv temp/
COPY temp/siruta_toate.csv temp/
COPY temp/siruta_cu_diacritice.csv temp/
COPY temp/zile_libere_legale_romania_2026.csv temp/
COPY temp/exchange_rates/ temp/exchange_rates/

# API files
COPY auth.py .
COPY main.py .
COPY init_db.py .
COPY manage_keys.py .
COPY routers/ routers/
COPY scripts/ scripts/
COPY docker/ docker/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
