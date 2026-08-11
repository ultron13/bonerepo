FROM python:3.12-slim
WORKDIR /srv
RUN pip install --no-cache-dir "fastapi>=0.111" "uvicorn[standard]>=0.30"
COPY images/demo-target/app.py /srv/app.py
RUN useradd --uid 10001 --no-create-home demo
USER 10001
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
