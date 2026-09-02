FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN pip install --no-cache-dir pipenv

# Dependencies first, so a source change does not invalidate the install layer.
# --system: install into the image's own site-packages rather than nesting a
#           virtualenv inside an already-isolated container.
# --deploy: fail the build if Pipfile.lock has drifted from Pipfile.
# --dev installs [dev-packages] too. This image *is* the development environment
# for this project — the host runs Python 3.8, so mypy and friends cannot run
# outside a container. A production build would drop --dev.
COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy --dev

RUN useradd --create-home --uid 1000 appuser
COPY . .
RUN chown -R appuser:appuser /app
USER appuser

# Phase 1 has no server to run; the container exists so migrations and seeds
# have a place to run with the right interpreter and network access.
CMD ["sleep", "infinity"]
