# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
#
# Scriptavera's hosted service (scriptavera.com) for Google Cloud Run.
#   docker build -t scriptavera .                      (add --build-arg WITH_LATEX=0 for a much smaller image
#   docker run -p 8080:8080 -e PORT=8080 scriptavera    without PDFs or redrawn figures)
# Python 3.11, in its slim variant: the same interpreter as python:3.11, without the compilers and libraries the
# app never uses (about 150 MB instead of about 1 GB before LaTeX).
FROM python:3.11-slim

ARG WITH_LATEX=1
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SCRIPTAVERA_PUBLIC=1 \
    SCRIPTAVERA_NO_BROWSER=1 \
    PORT=8080

# pdfLaTeX compiles the PDF of each paper and the redrawn figures (TikZ); without it the Overleaf package is still made.
RUN if [ "$WITH_LATEX" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended \
        texlive-science lmodern \
      && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/*; \
    fi

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# The app writes its papers (projects/), its signing key (identity/) and, without Supabase, accounts.db here.
# On Cloud Run these files live in the instance's memory.
RUN useradd --create-home --uid 10001 scriptavera && mkdir -p projects identity && chown -R scriptavera /app
USER scriptavera

EXPOSE 8080
CMD ["python", "app.py"]
