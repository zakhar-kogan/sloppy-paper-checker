FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY pyproject.toml ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --no-cache-dir .
RUN chown -R app:app /app
USER app
EXPOSE 8787
CMD ["uvicorn", "sloppy_checker.main:app", "--host", "0.0.0.0", "--port", "8787"]
