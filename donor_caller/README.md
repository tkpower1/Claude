# Stella Maris Academy - Local Business Donor Outreach Caller

Automated outbound calling system using Bland AI to contact La Jolla businesses
and solicit donations for the school auction/gala.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure `.env` with your Bland AI API key and callback number:
   ```
   BLAND_API_KEY=your_key_here
   CALLBACK_PHONE=+18585551234
   ```

## Usage

### Test a single call
```bash
python caller.py test --phone +18585551234
```

### Run the full campaign
```bash
# Call all businesses (30s delay between calls)
python caller.py call

# Call max 5 businesses
python caller.py call --max 5

# Custom delay between calls (60 seconds)
python caller.py call --delay 60

# Skip retrying voicemail/no-answer businesses
python caller.py call --no-retry
```

### Check campaign status
```bash
python caller.py status
```

## Files

- `caller.py` - Main calling script
- `businesses.csv` - La Jolla business contact database (50 businesses)
- `call_results.csv` - Auto-generated call results and tracking
- `.env` - API key and configuration (not committed)

## Business Categories

- Hotels & Resorts (5)
- Restaurants & Dining (18)
- Salons & Beauty (8)
- Spas & Wellness (3)
- Fitness Studios (4)
- Art Galleries (3)
- Surf/Beach/Recreation (2)
- Coffee/Bakery (4)
- Wine/Spirits (1)
- Bookstore/Gift (1)
- Florist (1)

## How It Works

1. Loads businesses from `businesses.csv`
2. Skips already-contacted businesses (interested or declined)
3. Calls each business via Bland AI with a friendly donation request script
4. Waits 90s for call completion, then fetches results
5. Logs everything to `call_results.csv`
6. Retries voicemail/no-answer up to 3 times
