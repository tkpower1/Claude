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

**Repeat for all 10 sections:**
- `aftersol-hero`
- `aftersol-problem`
- `aftersol-solution`
- `aftersol-how-it-works`
- `aftersol-what-you-get`
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
| 6 | AfterSol Social Proof |
| 7 | AfterSol Reviews |
| 8 | AfterSol Guarantee |
| 9 | AfterSol FAQ |
| 10 | AfterSol Final CTA |

6. Click **Save**

---

## Step 4: Configure Each Section

Click on each section in the customizer to configure:

### AfterSol Hero
- **Headline**: "Got Sunburned?<br>Tonight Is Going to Be Miserable."
- **Before Image**: Upload your sunburn image
- **After Image**: Upload your healed skin image
- **Button Link**: Set to your product page (e.g., `/products/aftersol-kit`)
- **Urgency Text**: Update stock count as needed

### AfterSol Solution
- **YouTube Video ID**: `naahPzd8CY0` (or your video ID)

### AfterSol How It Works
- **Button Link**: Set to your product page

### AfterSol Final CTA
- **Button Link**: Set to your product page

---

## Step 5: Create Your Product

1. Go to **Products** > **Add product**
2. Fill in:
   - **Title**: AfterSol Emergency Relief Kit
   - **Description**: Use copy from landing page
   - **Price**: $44.00
   - **Images**: Upload product photos
3. Save

### Recommended Product Description:

```
Stop sunburn pain in 30 minutes. Sleep through the night.

AfterSol is a two-step emergency relief system designed for exactly this moment. Not a lotion. Not a spray. A clinical-grade cooling system that actually works.

**What's Included:**
- 4 Hydrogel Cooling Sheets (immediate relief)
- 4 Recovery Fiber Sheets (overnight comfort)
- 4 complete treatments per kit
- Works on arms, legs, shoulders, back

**How It Works:**
1. Apply cooling hydrogel sheet to burned skin. Pain drops fast in 30 minutes.
2. Apply recovery sheet before bed. Wake up recovered, not wrecked.

**100% Money-Back Guarantee** - If AfterSol doesn't stop your sunburn pain, we'll refund every penny.
```

---

## Step 6: Update Button Links

After creating your product, update all CTA button links:

1. Go to **Themes** > **Customize**
2. Click each section that has a button
3. Set **Button Link** to your product (e.g., `shopify://products/aftersol-kit`)
4. Save

---

## Step 7: Remove Default Sections

In the customizer, remove any default Dawn theme sections you don't need:
- Image banner
- Featured collection
- Rich text
- etc.

Just click the section and click **Remove section**.

---

## Step 8: Configure Header/Footer

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
