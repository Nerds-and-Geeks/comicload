# comicload

Take photos of your comic covers. Get them into your League of Comic Geeks collection.

comicload looks at each photo, works out which comic it is, and builds a file you can
upload to League of Comic Geeks. Comics it cannot work out are set aside for you to
check, never guessed at.

## What you need first

**A Mac with Python 3.12 or newer.** Check by opening Terminal and running:

```bash
python3 --version
```

**One extra piece of software** for reading barcodes:

```bash
brew install zbar
```

If you do not have `brew`, install it from [brew.sh](https://brew.sh) first.

## Installing

```bash
pip install comicload
```

## Setting up, once

comicload identifies your comics by looking them up in a free public comic database
called the Grand Comics Database. You download it once, and after that comicload works
entirely on your own computer — no internet needed, no fees, nothing sent anywhere.

1. Make a free account at [comics.org](https://www.comics.org/) and download their data dump.
2. Point comicload at the downloaded file:

```bash
comicload catalog sync ~/Downloads/gcd_dump.sql
```

This takes a few minutes and only needs doing once. Repeat it every few months if you
want newer comics included.

## Cataloguing your comics

Put your photos in a folder — one comic per photo, the whole cover in frame.

```bash
comicload scan ~/Desktop/my-comics --out collection.csv
```

You will see a progress bar, then a summary like this:

```
      Scan results
┏━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Outcome        ┃ Count ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ Identified     │    47 │
│ Needs review   │     3 │
│ Not recognised │     1 │
└────────────────┴───────┘
```

followed by a short box confirming how many comics were written to `collection.csv`.

It helps a lot to tell comicload roughly what it is looking at:

```bash
comicload scan ~/Desktop/my-comics --publisher Marvel --years 1970-1985
```

## Checking the ones it was unsure about

```bash
comicload review
```

This shows every comic comicload could not identify confidently, with its best guess
if it has one, so you can sort them out yourself.

## Getting them into League of Comic Geeks

First, check the file is good. This does not upload anything:

```bash
comicload import collection.csv
```

If it says everything looks good, you have two ways to get your comics into League of
Comic Geeks.

### Upload it yourself

`collection.csv` is a normal file. Go to the League of Comic Geeks website, open their
own **Bulk Import** page, and upload it there. This always works today and needs
nothing extra installed.

### Let comicload upload it for you

```bash
comicload import collection.csv --import-locg
```

This feature is not finished yet. Today it will tell you to run
`pip install 'comicload[locg]'`, and automatic uploading still is not available even
after that. Until it is finished, use the Bulk Import page above.

## Settings

```bash
comicload config show           # see your current settings
comicload config init           # create a settings file
comicload config keys <name>    # store an API key safely in your keychain
```

## Taking good photos

- One comic per photo
- Whole cover in frame
- As flat-on as you can manage
- Avoid glare across the barcode — that is what comicload reads first
- Comics from before about 1975 have no barcode, so they are harder and more likely to
  need review

## Getting help

If something is not working, run the command again with the folder path in quotes, and
check the message comicload prints — it tries to say plainly what went wrong.
