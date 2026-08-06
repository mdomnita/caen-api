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
COPY temp/posta-romana/coduri-postale-romania/infocod-mai-2016_siruta.csv temp/posta-romana/coduri-postale-romania/
COPY temp/posta-romana/coduri-postale-romania/infocod-mai-2016_orase_siruta.csv temp/posta-romana/coduri-postale-romania/
COPY temp/posta-romana/coduri-postale-romania/infocod-mai-2016_sate_siruta.csv temp/posta-romana/coduri-postale-romania/

# API files
COPY auth.py .
COPY main.py .
COPY init_db.py .
COPY caen.db .
COPY manage_keys.py .
COPY api_dependencies.py .
COPY routers/ routers/
COPY helpers/ helpers/
COPY services/ services/
COPY scripts/ scripts/
COPY docker/ docker/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
