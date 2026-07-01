# MLflow server with PostgreSQL support
FROM ghcr.io/mlflow/mlflow:v2.13.0

USER root
RUN pip install --no-cache-dir psycopg2-binary

USER mlflow
