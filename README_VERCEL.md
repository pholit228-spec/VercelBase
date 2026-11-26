# Telegram bot on Vercel + PostgreSQL

## Required
- BOT_TOKEN — add manually in Vercel.

## Database
Connect Neon through Vercel Marketplace. It supplies DATABASE_URL automatically.
The bundled database.db is imported into PostgreSQL once on first startup.

## Deploy
Connect this repository to Vercel and add only BOT_TOKEN manually.
No WEBHOOK_URL or DB_PATH variable is required.
