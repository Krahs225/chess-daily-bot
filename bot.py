name: Daily Chess Puzzle

on:
  schedule:
    - cron: "0 9 * * *"   # 10:00 NL tijd (GitHub = UTC)
  workflow_dispatch:

jobs:
  run-bot:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install -r requirements.txt

      - name: Download Lichess puzzle database
        run: |
          curl -L https://database.lichess.org/lichess_db_puzzle.csv.zst -o puzzles.zst
          sudo apt-get install -y zstd
          zstd -d puzzles.zst
          mv lichess_db_puzzle.csv lichess_db_puzzle.csv

      - name: Run bot
        env:
          DISCORD_TOKEN: ${{ secrets.DISCORD_TOKEN }}
        run: |
          python bot.py
