# syntax=docker/dockerfile:1

# AstrBot is kept at the version used by the dev harness. The candidate
# plugin and templates are mounted by Compose from the checkout at run time;
# this keeps every run fresh instead of baking a stale candidate into the
# image. Dependencies are installed below from the candidate requirements.
FROM soulter/astrbot:v4.28.1

USER root

COPY bot/requirements.txt /tmp/alicedev-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/alicedev-requirements.txt \
    && rm /tmp/alicedev-requirements.txt

COPY deploy/e2e/seed-data/ /AstrBot/data/

RUN mkdir -p \
      /AstrBot/data/plugin_data/alicedev/images \
      /AstrBot/data/plugin_data/alicedev/reports
