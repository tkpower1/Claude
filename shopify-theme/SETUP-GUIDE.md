# AfterSol Shopify Theme Setup Guide

This guide walks you through setting up your AfterSol Shopify store using the custom sections we've created.

## Quick Overview

| File | Description |
|------|-------------|
| `assets/aftersol-custom.css` | All custom styles |
| `sections/aftersol-hero.liquid` | Hero with before/after images |
| `sections/aftersol-problem.liquid` | Problem section |
| `sections/aftersol-solution.liquid` | Solution with video |
| `sections/aftersol-how-it-works.liquid` | Two-step process |
| `sections/aftersol-what-you-get.liquid` | Kit contents & pricing |
| `sections/aftersol-social-proof.liquid` | Photo grid |
| `sections/aftersol-reviews.liquid` | Customer reviews |
| `sections/aftersol-guarantee.liquid` | Money-back guarantee |
| `sections/aftersol-product-cards.liquid` | 3-SKU product picker |
| `sections/aftersol-faq.liquid` | FAQ accordion |
| `sections/aftersol-final-cta.liquid` | Final call to action |

---

## Step 1: Add the CSS File

1. Go to **Shopify Admin** > **Online Store** > **Themes**
2. Click **Actions** > **Edit code** on your active theme
3. In the left sidebar, find **Assets** folder
4. Click **Add a new asset**
5. Create a new blank file named `aftersol-custom.css`
6. Copy/paste the contents of `assets/aftersol-custom.css`
7. Click **Save**

### Include CSS in Your Theme

1. In the left sidebar, find **Layout** > `theme.liquid`
2. Find the `</head>` tag
3. Add this line just BEFORE `</head>`:

```liquid
{{ 'aftersol-custom.css' | asset_url | stylesheet_tag }}
```

4. Click **Save**

---

## Step 2: Add Custom Sections

For each section file in the `sections/` folder:

1. In **Edit code**, find the **Sections** folder
2. Click **Add a new section**
3. Name it exactly as the file (e.g., `aftersol-hero`)
4. Delete the default code
5. Copy/paste the entire contents of the corresponding `.liquid` file
6. Click **Save**

**Repeat for all 11 sections:**
- `aftersol-hero`
- `aftersol-problem`
- `aftersol-solution`
- `aftersol-how-it-works`
- `aftersol-what-you-get`
- `aftersol-product-cards`
- `aftersol-social-proof`
- `aftersol-reviews`
- `aftersol-guarantee`
- `aftersol-faq`
- `aftersol-final-cta`

---

## Step 3: Build Your Homepage

1. Go to **Online Store** > **Themes**
2. Click **Customize** on your active theme
3. On the left sidebar, under **Template**, ensure you're on **Home page**
4. Click **Add section**
5. Find and add sections in this order:

| Order | Section Name |
|-------|--------------|
| 1 | AfterSol Hero |
| 2 | AfterSol Problem |
| 3 | AfterSol Solution |
| 4 | AfterSol How It Works |
| 5 | AfterSol What You Get |
| 6 | AfterSol Product Cards |
| 7 | AfterSol Social Proof |
| 8 | AfterSol Reviews |
| 9 | AfterSol Guarantee |
| 10 | AfterSol FAQ |
| 11 | AfterSol Final CTA |

6. Click **Save**

---

## Step 4: Configure Each Section

Click on each section in the customizer to configure:

### AfterSol Hero
- **Headline**: "Got Sunburned?<br>Tonight Is Going to Be Miserable."
- **Before Image**: Upload your sunburn image
- **After Image**: Upload your healed skin image
- **Button Link**: Set to your collection (e.g., `/collections/aftersol`)
- **Urgency Text**: Update stock count as needed

### AfterSol Solution
- **YouTube Video ID**: `naahPzd8CY0` (or your video ID)

### AfterSol How It Works
- **Button Link**: Set to your collection page

### AfterSol Final CTA
- **Button Link**: Set to your collection page

---

## Step 5: Create Your Products (3 SKUs)

You need to create **3 separate products** in Shopify — one for each kit size.

### Product 1: AfterSol Try It (2-Pack)

1. Go to **Products** > **Add product**
2. Fill in:
   - **Title**: AfterSol Try It — 2-Pack
   - **Price**: $10.00
   - **SKU**: `aftersol-2pack`
   - **Images**: Upload product photos
   - **Description**:

```
Try AfterSol risk-free. One complete sunburn treatment.

Includes:
- 1 Hydrogel Cooling Sheet (immediate relief — apply for 30 minutes)
- 1 Recovery Fiber Sheet (overnight comfort — apply before bed)

Stop sunburn pain in 30 minutes. Sleep through the night. Works on arms, legs, shoulders, back.

100% Money-Back Guarantee.
```

3. Save

### Product 2: AfterSol Weekend Pack (4-Pack)

1. Go to **Products** > **Add product**
2. Fill in:
   - **Title**: AfterSol Weekend Pack — 4-Pack
   - **Price**: $25.00
   - **SKU**: `aftersol-4pack`
   - **Images**: Upload product photos
   - **Description**:

```
Two complete sunburn treatments — perfect for a weekend trip.

Includes:
- 2 Hydrogel Cooling Sheets (immediate relief)
- 2 Recovery Fiber Sheets (overnight comfort)

$12.50 per treatment. Stop sunburn pain in 30 minutes. Sleep through the night.

100% Money-Back Guarantee.
```

3. Save

### Product 3: AfterSol Full Kit — 8-Pack (Best Value)

1. Go to **Products** > **Add product**
2. Fill in:
   - **Title**: AfterSol Full Kit — 8-Pack
   - **Price**: $35.00 (was $44)
   - **Compare at price**: $44.00
   - **SKU**: `aftersol-8pack`
   - **Images**: Upload product photos
   - **Description**:

```
The full AfterSol emergency relief system. Four complete treatments at the best price.

Includes:
- 4 Hydrogel Cooling Sheets (immediate relief)
- 4 Recovery Fiber Sheets (overnight comfort)
- 4 complete treatments

Just $4.38 per treatment — save $9 vs. buying individually. Works on arms, legs, shoulders, back.

100% Money-Back Guarantee.
```

3. Save

---

## Step 6: Create the AfterSol Collection

1. Go to **Products** > **Collections**
2. Click **Create collection**
3. Fill in:
   - **Title**: AfterSol
   - **Description**: Sunburn relief kits — stop the pain in 30 minutes, sleep through the night.
   - **Collection type**: Manual
4. Under **Products**, add all 3 AfterSol products:
   - AfterSol Try It — 2-Pack
   - AfterSol Weekend Pack — 4-Pack
   - AfterSol Full Kit — 8-Pack
5. **Sort order**: Set to "Manually" and drag to order: 2-Pack, 4-Pack, 8-Pack
6. Upload a collection image
7. Save

The collection will be accessible at `/collections/aftersol` — this is where all CTA buttons link by default.

---

## Step 7: Configure Inventory

For each of the 3 products:

1. Go to **Products** and click the product
2. Scroll to **Inventory** section
3. Check **Track quantity**
4. Set initial stock quantity
5. Optionally enable **Continue selling when out of stock** if you want to allow backorders
6. Save

---

## Step 8: Update Button Links

After creating your products and collection, update all CTA button links:

1. Go to **Themes** > **Customize**
2. Click each section that has a button
3. Set **Button Link** to your collection: `shopify://collections/aftersol`
4. For the **Product Cards** section, set each card's link to its specific product:
   - Card 1: `shopify://products/aftersol-try-it-2-pack`
   - Card 2: `shopify://products/aftersol-weekend-pack-4-pack`
   - Card 3: `shopify://products/aftersol-full-kit-8-pack`
5. Save

---

## Step 9: Remove Default Sections

In the customizer, remove any default Dawn theme sections you don't need:
- Image banner
- Featured collection
- Rich text
- etc.

Just click the section and click **Remove section**.

---

## Step 10: Configure Header/Footer

### Header
1. Click on **Header** in customizer
2. Upload your logo (or keep text logo)
3. Add a "Shop Now" button linking to your product

### Footer
1. Click on **Footer**
2. Add your support email: support@aftersol.co
3. Add copyright: © 2026 AfterSol

---

## Image URLs (For Reference)

If you haven't uploaded images yet, these URLs are pre-configured:

**Before/After Images:**
- Before: `https://i.imgur.com/wiCkThCl.jpg`
- After: `https://i.imgur.com/zaev3B6l.jpg`

**Product Photos:**
- `https://i.imgur.com/s8oQuEzm.jpg`
- `https://i.imgur.com/R5Y7m5Rm.jpg`
- `https://i.imgur.com/vR3p4FYm.jpg`
- `https://i.imgur.com/1bjv4Cmm.jpg`

**Video Thumbnail:**
- `https://img.youtube.com/vi/naahPzd8CY0/hqdefault.jpg`

---

## Brand Colors (For Other Customizations)

| Color | Hex Code | Usage |
|-------|----------|-------|
| Primary Orange | `#e85d2a` | CTAs, accents |
| Dark | `#1a1a1a` | Text, headers |
| Black | `#111111` | Dark sections |
| Success Green | `#22964f` | Checkmarks, badges |
| Gray | `#666666` | Body text |

---

## Troubleshooting

### Sections not showing?
- Make sure you saved the CSS file
- Check that CSS is included in `theme.liquid` before `</head>`
- Refresh your browser cache

### Styles look wrong?
- Clear your browser cache
- Make sure section names match exactly
- Check for typos in the CSS include

### FAQ accordion not working?
- JavaScript is included in the FAQ section file
- Make sure you copied the entire file including the `<script>` tag

---

## Need Help?

- Shopify Help: https://help.shopify.com
- AfterSol Support: support@aftersol.co

---

**You're all set!** Your AfterSol store should now match your high-converting landing page design.
