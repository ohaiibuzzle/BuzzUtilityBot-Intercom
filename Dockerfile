FROM python:3.11-slim

# Install requirements
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Cache busting
ARG CACHEBUST=1

RUN mkdir /app 
COPY src /app/src

# require that the user provide /app/runtime as a volume
VOLUME /app/runtime

WORKDIR /app
CMD ["python3", "src/main.py"]
