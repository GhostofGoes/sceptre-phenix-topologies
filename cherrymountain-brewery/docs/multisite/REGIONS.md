# CherryMountain Brewing: the four sites' utilities, prices, and rules

> **Status:** research for the `fleet` variant of
> [PLANS.md](PLANS.md), read from primary sources on 2026-11-23 unless a row
> says otherwise. Nothing here is built. The company is notional; the places,
> utilities, prices, and rules are real, and each figure carries its source.
>
> **tl;dr:** the four sites are on Colorado's Front Range, and they differ more
> by their utilities and city rules than by their weather. `fr` and `ep` share
> an electric utility and a sewer district; `cs` and `fc` each buy their power,
> water, and sewer from a utility the city owns. Three differences reach the plant's control
> logic: the bulk CO₂ shutoff point, how each bill rewards keeping the boiler
> off the evening peak, and the water and sewer rules a drought or a
> pretreatment program can impose. A fourth reaches the company itself:
> Colorado does not let the same owners hold a brewery licence and a brew pub
> licence.

## Contents

1. [How to read this](#1-how-to-read-this)
2. [The four places side by side](#2-the-four-places-side-by-side)
3. [`fr`: Front Range (Denver)](#3-fr-front-range-denver)
4. [`ep`: Eastern Plains, near Aurora](#4-ep-eastern-plains-near-aurora)
5. [`cs`: Colorado Springs](#5-cs-colorado-springs)
6. [`fc`: Fort Collins](#6-fc-fort-collins)
7. [Breweries and a pub: licences and records](#7-breweries-and-a-pub-licences-and-records)
8. [What the places change in the design](#8-what-the-places-change-in-the-design)
9. [References](#9-references)

## 1. How to read this

- **Sources.** Each table row names its source in brackets: `[EP12]` is
  item 12 of the Eastern Plains list in [section 9](#9-references). A source
  marked *secondary* is a copy, a summary, or the press, used because the
  primary could not be read; the row says so.
- **Computed** marks arithmetic of ours on cited values.
- **Not verified** lists close each site's section. Several code sites
  refused automated reading (Aurora's, Colorado Springs', Fort Collins's on
  Municode, Poudre Fire Authority's, and the ICC's), so some rules were read
  through the city's own pages, a copy, or a search engine's excerpt of the
  page, which the row says.
- **Sizes.** The bills and fees are for the plant of
  [DESIGN.md §3.1](../DESIGN.md#31-site-and-capacity) and
  [§7.2](../DESIGN.md#72-loads-notional-checked-against-54): a peak of about
  250 to 300 kW from a 200 kW electric boiler, a load factor of 14 to 17 %,
  and roughly 1,000 to 1,700 gallons of water a day. `fc` is smaller.

## 2. The four places side by side

| | `fr` Front Range | `ep` Eastern Plains | `cs` Colorado Springs | `fc` Fort Collins |
|---|---|---|---|---|
| Stands for | Denver, the jurisdiction of [DESIGN.md](../DESIGN.md) | Aurora's east side, near I-70 and E-470 | Colorado Springs | Fort Collins, the old town |
| County | Denver | Adams | El Paso | Larimer |
| Elevation; water boils at | about 5,200 ft; about 202 °F | 5,514 ft; about 202 °F | 6,035 ft; about 201 °F | 4,983 ft; about 203 °F |
| Weather file (TMY3) | Denver | Aurora Buckley Field (724695) | Colorado Springs (724660) | Fort Collins AWOS (724769) |
| Electric utility; rates set by | Xcel Energy; the PUC | Xcel Energy; the PUC | Colorado Springs Utilities; the City Council as its board | Fort Collins Utilities; the City Council, by ordinance |
| Schedule | SV-SGT, the design's notional blend | SG | EIS | GS50 (E300) |
| What the electric bill rewards | Low demand at every hour, and energy off the evening window | Low demand at every hour; by time of day from the April after the meter is fitted | Low on-peak demand by the day (17:00 to 21:00); a 68 % ratchet off-peak | A low load in PRPA's one peak hour of the month; a 70 % ratchet |
| Water | Denver Water | Aurora Water: an allocation, and a higher price above it; Stage 1 drought since 2026-04-06 | CSU; shortage preparation since 2026-03-18; cuts in indoor industrial use at Stages III and IV | FCU: an annual allotment, and $15.59 per 1,000 gal above it; a voluntary Watch since 2026-05-01 |
| Sewer and pretreatment | Metro Water Recovery; pH under 5.0 prohibited | Aurora's sewers, Metro's treatment and rules; the bill set by the winter quarter | CSU; pH under 5.5 prohibited, with a recorder | The city's own; enforceable brewery practices |
| Fire authority; bulk CO₂ shutoff | Denver Fire Department; at 30,000 ppm | Aurora Fire Rescue; none required | Colorado Springs Fire Department; at 5,000 ppm or loss of power | Poudre Fire Authority; not known |
| Codes | As [DESIGN.md §18A](../DESIGN.md#18a-regulatory-and-standards-context) | 2021 I-codes | 2021 IFC; 2023 regional building code | 2024 I-codes, from 2026-04-01 |
| Sales tax, combined | Not researched | 8.50 % | 8.20 % | 8.30 % |

`fr` is as [DESIGN.md](../DESIGN.md) has it; [section 3](#3-fr-front-range-denver)
adds two notes on its tariff. Each row of the other three is sourced in that
site's section.

## 3. `fr`: Front Range (Denver)

The flagship stays as [DESIGN.md](../DESIGN.md) has it. Denver stands for its
jurisdiction: Xcel Energy for electricity, Denver Water, Metro Water Recovery
for treatment and pretreatment, the Denver Fire Department's CO₂ policy, and
Energize Denver ([DESIGN.md §7.4](../DESIGN.md#74-utility-tariff-from-open-data),
[§18A](../DESIGN.md#18a-regulatory-and-standards-context)).

Two things the regional research adds:

- **`ep` has the same electric utility.** Xcel's rates summary of 2026-10-19
  gives Schedule SG's generation and transmission demand, flat, as $26.63 per
  kW in summer and $19.45 in winter *with riders*, and $15.14 and $9.11
  without them [EP1], [EP2]. The notional tariff SV-SGT of DESIGN.md §7.4
  charges $27.14 and $19.79, which is close to the totals with riders, while
  §7.4 says its values are "without riders or adjustments". One of the two
  statements is wrong; URDB may record the totals. To check against the URDB
  record before the tariff is reused.
- **The time-of-day window moved.** SG's demand by time of day is charged
  from 16:00 to 21:00 in summer and 17:00 to 21:00 in winter (sheet 43B, from
  2025-11-01) [EP2]. SV-SGT uses 17:00 to 21:00 all year, from the
  small-commercial SG-TOU. A brew day that ends its boils by 15:30 is outside
  both.

## 4. `ep`: Eastern Plains, near Aurora

The production brewery, on the industrial land of Aurora's east side, near
I-70 and E-470. It has the same electric utility and the same sewer district
as the Front Range site, and a very different water utility.

### 4.1 The place

| Item | Value | Source |
|---|---|---|
| County | Adams, inside the Aurora city limits (the city also reaches into Arapahoe and Douglas) | [EP31] |
| Elevation | 5,514 ft at I-70 and E-470; 5,449 to 5,468 ft at three other points nearby | [EP31] |
| Water boils at | about 202 °F (94.4 °C), computed for 1,670 m | computed |
| Weather file | TMY3 "Aurora Buckley Field ANGB", station 724695 (class II data); Denver International, 725650, is the class I alternative | [EP30] |

The points are sample coordinates, not a surveyed site.

### 4.2 Electricity

The I-70 and E-470 area is in the territory of Xcel Energy (Public Service
Company of Colorado): its tariff lists Aurora as a franchise city, and the
city calls Xcel "the main electric and gas utility serving the City of
Aurora" [EP2], [EP11]. CORE Electric Cooperative serves Bennett, further
east, and land to the south. The line between them was not found at parcel
scale.

So this site has the Front Range site's utility, and takes **Schedule SG**
(Secondary General) [EP1], [EP2]:

| Component | Base rate | With riders, from 2026-10-19 |
|---|---|---|
| Service and facility | $88.07 a month | $105.76 |
| Distribution demand: the greater of the month's peak or half of the peak of the last twelve months | $9.36 per kW | $11.15 |
| Generation and transmission demand, flat: winter, summer | $9.11, $15.14 per kW | $19.45, $26.63 |
| Generation and transmission demand, by time of day: winter, summer | $10.74, $17.86 per kW | $21.39, $29.87 |
| Energy | $0.00853 per kWh | $0.03689 |

- **Summer** is June 1 to September 30.
- **The time-of-day window** is 16:00 to 21:00 in summer and 17:00 to 21:00
  in winter, on weekdays that are not holidays (sheet 43B, from 2025-11-01).
- **A new site starts on flat demand.** A customer whose advanced meter
  arrives in 2026 or later moves to demand by time of day in the billing cycle
  that holds April 1 of the next year (sheet 43A).
- **The other schedules are closed to this plant.** SGL excludes net-metered
  customers. The SG-TOU pilot, SPVTOU, and SG-CPP each need a load factor of
  30 %, and this plant's is 14 to 17 % (sheets 44, 45, 49A, 51).
- **For the range:** `ep` uses the notional tariff of
  [DESIGN.md §7.4](../DESIGN.md#74-utility-tariff-from-open-data), which was
  built from this utility's schedules. The real SG differs from it in two
  ways that matter: energy is under 4 cents a kilowatt-hour, and the summer
  window opens an hour earlier.

If the parcel were CORE's, everything here would change: Large Power Service
at $165.00 a month, $17.50 per kW, and $0.07224 per kWh in 2026 ($177.79,
$18.86, and $0.07784 from 2027-01-01), with rates set by the cooperative's
board [EP4], [EP5].

### 4.3 Generation on site

`ep` has the solar array, the battery, and the generator that `fr` has.

| Rule | Xcel (the PUC's rules) | CORE, for contrast |
|---|---|---|
| Net metering size | Up to 200 % of expected annual consumption, with no kW cap (Schedule NM, sheet 112) [EP2] | 25 kW for a commercial customer (C.R.S. 40-9.5-118(2)(e)(I)) [EP27], [EP5] |
| Credit | One for one, carried forward; at the year's end, cash at the average hourly incremental cost or a dollar credit at the energy rate, about $0.037/kWh on SG (sheet 112A) [EP2] | A 100 kW array is over the cap: exports are paid at avoided cost, $0.04616/kWh [EP7] |
| Battery | Reviewed with the array under one agreement; export can be limited by a power control system (4 CCR 723-3, Rule 3853(c)) [EP28] | No small-generator service for a customer who charges a battery from the grid [EP5] |
| Process | Over 25 kW is Level 2, the fast track; the fee is $475 (sheet R232) [EP2] | Level 2; $500 [EP5] |
| **Telemetry** | From 25 to 250 kW, Xcel "may require" remote monitoring of real and reactive power, voltage, and status. From 250 kW it will. From 1 MW, telemetry and a remote disconnect or curtailment (DG manual §8.4, 2021) [EP3] | "All level 2 interconnections will require telemetry" [EP8] |
| Standby generator | An interlocked, open-transition transfer switch makes it a "separate system" [EP3] | No parallel operation; a lockable disconnect near the meter [EP9] |

- **The utility's DNP3 link is an assumption.** Neither utility's published
  documents name DNP3 for a site this size. Xcel may ask for monitoring at
  100 kW, and its battery program (Schedule AVPP, sheet 124A, from
  2026-04-01) asks for the interoperability of IEEE 1547-2018 clause 10 and
  takes batteries up to 750 kW through an aggregator [EP2]. The link of
  [DESIGN.md §7.5](../DESIGN.md#75-utility-der-link) stays a teaching
  device, at `fr` and here.
- **The boiler is the lever.** Once on demand by time of day, keeping the
  200 kW boiler out of the weekday window avoids the $21 to $30 per kW
  charge. A kilowatt-hour from the roof is worth under 4 cents.

### 4.4 Water

Aurora Water prices a commercial customer against an allocation, and decides
before it connects a large user whether it wants the load.

| Item | 2026 value | Source |
|---|---|---|
| Tier I, up to the allocation | $7.11 per 1,000 gal | [EP12] |
| Tier II, above the allocation, for the rest of the year | $12.59 per 1,000 gal | [EP12], [EP17] |
| Base charge, 1.5 in or 2 in meter | $39.54 or $57.44 a month | [EP12] |
| Connection fee | $93.29 for each gallon a day of recoverable use, $130.47 for each gallon a day that does not come back to the sewer | [EP13] |
| Stage 1 surcharge | $2.15 per 1,000 gal on use above 110 % of the winter-quarter average | [EP15] |

- **The allocation** comes from the figures given when the site is planned,
  or from history. Use beyond it "may be addressed through monthly bill or
  payment of additional connection fees" [EP13], [EP17].
- **What leaves in the beer is the dear part.** Water that goes out in the
  product or up the kettle stack is not recoverable, and its connection fee
  is 40 % higher. At 1,000 to 1,700 gal a day and 70 % recoverable, the water
  connection fee alone is about $104,000 to $178,000 (computed).
- **Large users are screened.** The Large Water Users Guide (appendix F,
  2026-01) sorts a site by gallons per acre per day and by its share of
  non-recoverable use. For 26 to 50 % non-recoverable: up to 1,000 is
  allowed; 1,001 to 3,000 is allowed with notes; 3,001 to 3,500 needs an
  exemption; more is refused. It adds that "high strength wastewater can
  impact treatment performance … and reduce the recoverability of the
  water", which describes a brewery [EP14].
- **The city is in a drought stage.** Stage 1 was declared on 2026-04-06, and
  storage was 38 % on 2026-11-12. In Stage 1 the general manager "may require
  large commercial/industrial non-recoverable water users to reduce water
  use", and watering is two days a week [EP15], [EP16].
- **Reported, not read in the ordinance:** the council banned evaporative
  cooling in new commercial and industrial buildings in September 2026, and
  2027 rates are proposed at +2 % for water and +6.5 % for sewer [EP33].

### 4.5 Wastewater

Aurora Water owns the sewers. Metro Water Recovery treats the flow and runs
the pretreatment program, under the rules that apply to the Front Range site
([DESIGN.md §18A.4](../DESIGN.md#18a4-environment)) [EP19], [EP21].

| Item | Value | Source |
|---|---|---|
| Sewer usage | $5.46 per 1,000 gal, on the **winter-quarter average** (December to February) | [EP12], [EP18] |
| Base charge, 1.5 in or 2 in | $29.73 or $47.58 a month | [EP12] |
| Connection fee, 1.5 in meter | $70,520 to Aurora and $66,770 to Metro | [EP13] |
| Strength | No BOD or TSS surcharge is in Aurora's rate table. Metro prices strength in its connection charge, from the peak month's flow and pounds of BOD, solids, and nitrogen (rule 7.5.1) | [EP12], [EP19] |
| pH | Below 5.0 is prohibited; there is no number for the upper limit | Metro rules §6 [EP19] |
| Slug | Not more than five times the 24-hour average concentration or flow for more than 15 minutes | [EP19] |
| Oil and grease | 200 mg/L, by permit | Rule 6.18.2 [EP19] |
| Significant industrial user | 25,000 gal a day of process wastewater, or by designation | Rules §2, definition 85 [EP19] |
| What to file | Metro's Wastewater Discharge Information Form | [EP20] |

- **Winter sets the year's sewer bill.** What the plant uses from December to
  February is what it pays sewer on all year.
- Metro's rules name "beer and distillery slops, grain processing wastes"
  among the solids that may not go down the drain, and prohibit dilution
  (rule 6.18.5) [EP19].

### 4.6 Fire, building, and energy rules

| Rule | What it says | Source |
|---|---|---|
| Codes | 2021 International Building, Fire, Mechanical, and Energy codes, and the 2023 NEC; no plan to adopt the 2024 codes before about 2027 | [EP22], [EP24] |
| Fire authority | Aurora Fire Rescue | [EP23] |
| **CO₂** | No local policy was found, so the 2021 IFC §5307 applies as written: for more than 100 lb, ventilation or detection with sensors within 12 in of the floor, a supervisory alarm at 5,000 ppm and a local alarm at 30,000 ppm. It asks for no automatic shutoff | [EP25], secondary |
| Building energy | No city ordinance was found. Colorado's program covers buildings of 50,000 sq ft and more, and leaves out a building that is more than half manufacturing (C.R.S. 25-7-142(2)(j)) | [EP26] |
| Stormwater | $12.18 a month for the first 2,500 sq ft of impervious area and $9.60 for each 2,500 sq ft more | [EP12] |
| Airports | The site is in the airport influence district: an avigation easement is recorded before a permit, and a crane near Buckley can need 60 days of approval | [EP32] |
| Liquor | Aurora's local licensing authority hears local licences; the manufacturer's licence is the state's, and a tasting room is a sales room on which the city may object | [EP29] |
| Tax | 8.50 % combined sales tax in the Adams County part (8.00 % in Arapahoe); the city's head tax was repealed on 2025-01-01 | [EP29] |

### 4.7 Not verified

- Aurora's City Code and development code: the city's code site refused the
  request. The fire code amendments, the drought sections, and the sewer
  sections were read only through the city's own pages and rate tables.
- The line between Xcel's and CORE's territory at parcel scale.
- The current text of PUC Rule 3664: a copy still says 120 % where Xcel's
  tariff says 200 %.
- Xcel's franchise fee in Aurora, Metro's 2026 connection-charge fractions,
  and the adoption of Aurora's 2027 rates.
- Air permitting and testing rules for the diesel generator.

## 5. `cs`: Colorado Springs

A brewery and taproom that the company bought. One city-owned utility supplies
it with electricity, gas, water, and sewer.

### 5.1 The place

| Item | Value | Source |
|---|---|---|
| County | El Paso | [CS36] |
| Elevation | 6,035 ft (the city's figure); the weather station is at 6,142 ft | [CS37], [CS38] |
| Standard pressure at the station | 80,767 Pa (11.71 psia); Denver's airport station has 83,011 Pa | [CS38] |
| Water boils at | about 201 °F (93.8 °C), computed from that pressure | computed |
| Weather file | TMY3 "Colorado Springs Muni AP", station 724660 | [CS38] |
| Summer design day | 90.3 °F dry bulb (0.4 %), against 94.3 °F at Denver's airport: a smaller cooling and glycol load | [CS38] |
| Winter design day | −0.8 °F (99.6 %) | [CS38] |
| Climate zone | 5B | [CS38] |

### 5.2 Electricity

Colorado Springs Utilities (CSU) is a municipal utility. The City Council sits
as its Utilities Board and adopts each tariff by resolution; the Colorado PUC
does not set its rates [CS20], [CS21]. It joined the Southwest Power Pool's
market on 2026-04-01 [CS22].

A customer whose demand reaches 100 kW and stays under 500 kW takes
**Industrial Service, 100 kW Minimum** (EIS, or the optional EIS-P), tariff
sheet 7.1 [CS2].

| Component | 2026 | 2027, adopted | Sheet |
|---|---|---|---|
| Access and facilities | $3.7205 a day | $3.9400 a day | 2.7 |
| EIS demand, on-peak | $0.8817 per kW a day | $0.9337 | 2.7 |
| EIS demand, off-peak | $0.5732 per kW a day | $0.6070 | 2.7 |
| EIS base energy | none | none | 2.7 |
| Electric cost adjustment (ECA) | $0.0372/kWh on-peak, $0.0186 off-peak (from 2026-10-01; it can change monthly) | floats | 2.17 |
| Electric capacity charge (ECC) | $0.0048/kWh | floats | 2.18 |
| EIS-P, the option | A lower demand charge ($0.3485 a kW-day in winter, $0.3777 in summer), energy by period ($0.0098 to $0.1566/kWh), and critical-peak events at $0.5849/kWh, at most 15 a year | higher by 6 % | 2.8, 3.2 |

- **On-peak is 17:00 to 21:00 on weekdays, all year**, without six holidays
  (sheets 3.1 and 3.2). That is the window of the Front Range tariff, so the
  brew day that ends its boils by 15:30 fits here too.
- **Demand** is the greatest 15-minute load, raised by 1 % for each 1 % of
  power factor under 95 %. Off-peak billing demand is the greater of the
  off-peak maximum or 68 % of the twelve-month maximum, less the on-peak
  billing demand (sheet 3). One bad brew day is paid for all year.
- **The bill is nearly all demand.** At a 300 kW peak in a 30-day month, EIS
  demand is about $7,900 with the boiler running on-peak and about $6,100
  with on-peak held to 100 kW (computed from the sheet).
- **Base rates rise in 2027** by 6.5 % for electricity and water, 9 % for
  wastewater, and 4 % for gas, in a five-year rate case that is already
  adopted [CS22].

### 5.3 Generation on site

| Rule | What it says | Source |
|---|---|---|
| Net metering cap | 150 kW AC for a commercial or industrial customer; larger "subject to approval" | Tariff sheet 20 [CS2] |
| Sizing | 120 % of annual consumption today; 200 % from 2027-04-01 | [CS3], [CS4] |
| Rate while net-metered | EIS and EIS-P are closed to net-metered customers until 2027-04-01, when industrial net-metered customers move to EIS | Sheets 7.1 and 3.6 [CS2], [CS3] |
| Export | Monthly one-for-one netting; the yearly excess is paid at $0.0195/kWh on-peak and $0.0180 off-peak | Sheets 20.1, 2.21 [CS2] |
| Telemetry | A remote terminal unit with status, analog, and control points only at 500 kW AC and more. From 150 kW to under 500 kW, an interval meter | Line Extension and Service Standards §13.02(c), §13.08 [CS6] |
| Disconnect | The customer's, visibly open, lockable by the utility, outside, within 10 ft of the meters | §13.02(a), §13.05(j) [CS6] |
| Standby generator, open transition | "No further coordination is required with Utilities" | §13.03(a) [CS6] |
| Standby generator, closed transition | An operating agreement, relay settings 60 days ahead, a backup trip within 200 ms | §13.03(b), §13.04 [CS6] |
| Reclosing | The customer must "protect their own sensitive electrical equipment that can be harmed by rapid reclosing" | §3.14 [CS6] |

- **No utility link at this size.** A 100 kW array here would need no
  telemetry and no utility control point. The utility's DNP3 link at `fr`
  and `ep` has no counterpart at `cs`, whatever the site later installs.
- **Solar is worth little on EIS.** With no base energy charge, a kilowatt-hour
  from the roof saves only the ECA and ECC, about 2 to 4 cents, unless it also
  lowers billed demand (an inference from the tariff).
- **Outages can be long.** In high fire risk CSU's lines trip faster and stay
  off until a crew has patrolled them, "four to eight hours" [CS41]. That is
  the local reason for the standby generator that
  [PLANS.md 4.1](PLANS.md#41-what-each-site-is) leaves for later.

### 5.4 Water

| Item | 2026 | 2027, adopted | Source |
|---|---|---|---|
| Schedule | Nonresidential service, sheet 5 | | [CS10] |
| Service charge, meter under 2 in | $2.0905 a day | $2.2243 | Sheet 2.1 |
| Commodity, November to April | $0.0635 per cubic foot ($8.49 per 1,000 gal) | $0.0676 | Sheet 2.1 |
| Commodity, May to October | $0.0794 per cubic foot ($10.61 per 1,000 gal) | $0.0845 | Sheet 2.1 |
| Development charges, 1.5 in meter | $26,347 and a $19,135 water resource fee; they apply to a bought building only if the meter grows | | [CS8], [CS12] |

- **Watering rules** hold all year: three days a week at most, and no spray
  from 10:00 to 18:00 between May 1 and October 15 (City Code 12.4.1304)
  [CS13], [CS14].
- **A shortage can reach the brewhouse.** In Stage III, "Indoor industrial and
  commercial water use shall be reduced by an amount, to be determined by
  Utilities"; in Stage IV by up to 20 %, with no new connections (12.4.1306
  to 1309) [CS14]. The city has been in "water shortage preparation", the
  step before Stage I, since 2026-03-18 [CS15].

### 5.5 Wastewater

| Item | Value | Source |
|---|---|---|
| Schedule | Nonresidential (SC), billed on metered water | Wastewater tariff sheets 2 and 5 [CS11] |
| Service charge | $1.2104 a day (2027: $1.3193) | Sheet 2 |
| Quantity charge | $0.0380 per cubic foot, $5.08 per 1,000 gal (2027: $0.0414) | Sheet 2 |
| Strength surcharge | On BOD over 280 mg/L and TSS over 270 mg/L, about $0.30 and $0.15 a pound, but only for a user over 25,000 gal a day or a significant industrial user | Sheet 9 |
| Consumptive use adjustment | Credits on sub-metered water that does not reach the sewer: 45 % for bottling, 82 % for boiler make-up, 50 % or 100 % for water in the product | Sheets 5 to 5.3 |
| Program | CSU's Industrial Pretreatment Section, under City Code chapter 12, article 5 | [CS16], [CS17] |
| pH | Prohibited when the instantaneous pH is "less than 5.5 … or greater than or equal to 12.5"; a continuous recorder or approved sampling records are required | 12.5.702.N, from a copy of the code [CS19] |
| Slug | A doubling of flow for more than 20 minutes, or a pH violation for more than 30 minutes in a row; report at once and in writing within 5 days | 12.5.201, 12.5.807 [CS19] |
| Oil and grease | 100 mg/L total | 12.5.702.D [CS19] |
| Significant industrial user | 25,000 gal a day of process wastewater, 5 % of plant capacity, or by designation | 12.5.201 [CS19] |
| What to file | The Wastewater Discharge Questionnaire, within 30 days | [CS17] |
| The taproom's kitchen | A grease interceptor of at least 325 gal, or 1,000 gal with a dishwasher | [CS18] |

The plant discharges about 800 to 1,150 gal a day, far under the threshold,
so strength is not priced here unless CSU designates the brewery. It is
controlled by the ban on loads that interfere with the treatment plant.

### 5.6 Fire, building, and energy rules

| Rule | What it says | Source |
|---|---|---|
| Building code | 2023 Pikes Peak Regional Building Code, on the 2021 IBC and the 2023 NEC; the Pikes Peak Regional Building Department permits and inspects | [CS24] |
| Fire code | 2021 IFC with local amendments, Ordinance 23-14; Colorado Springs Fire Department | [CS26], [CS27] |
| CO₂, permit | An operational permit from 100 lb of CO₂ in an insulated liquid system | Table 105.5.9 as amended [CS26] |
| CO₂, detection | Sensors within 12 in of the floor, and one at each point of use away from the supply; a supervisory alarm at 5,000 ppm and a local alarm at 30,000 ppm | 5307.3.2 as amended [CS26], [CS28] |
| **CO₂, shutoff** | A fail-safe that "shuts off flow from the supply tank upon reaching 5,000 ppm … and/or loss of power". Without it, mechanical ventilation is required | 5307.3.2 item 3; the department's checklist [CS29] |
| Building energy | No city ordinance was found. Colorado's Regulation 28 covers buildings of 50,000 sq ft and more, so this building is not covered | [CS30], [CS31] |
| Liquor | The city's municipal judges are the local licensing authority, with the City Clerk as agent | City liquor rules [CS33] |
| Food | El Paso County Public Health licenses the taproom's kitchen | [CS35] |
| Sales tax | 8.20 % combined | [CS36] |

**The CO₂ shutoff is the rule that changes a setpoint.** Denver's policy shuts
the bulk supply at 30,000 ppm, and that is what `plc-utilities` does at `fr`.
Colorado Springs asks for the shutoff at 5,000 ppm.

### 5.7 Not verified

- The official text of City Code chapter 12, article 5: the city's code site
  refused the request. The limits above are from a copy attached to another
  authority's resolution and from CSU documents that quote the code.
- The stormwater fee after 2023 ($45 an acre a month in 2023) [CS23].
- The council resolution for the 2026-09-22 net-metering change, and the final
  adoption of the 2027 budget.
- Air permitting for a diesel generator, which was not researched.

## 6. `fc`: Fort Collins

The brewpub, in the old town. Like Colorado Springs, the city owns its
utilities: Fort Collins Utilities (FCU) supplies electricity, water,
wastewater, and stormwater, and the city also sells fiber broadband. Gas
comes from Xcel Energy [FC19], [FC36].

### 6.1 The place

| Item | Value | Source |
|---|---|---|
| County | Larimer | [FC22] |
| Elevation | 4,983 ft at 40.5880 N, 105.0765 W, near Old Town Square | [FC26] |
| Water boils at | about 203 °F (95 °C) | computed |
| Weather file | TMY3 "Fort Collins AWOS" (Fort Collins–Loveland Municipal Airport), station 724769, about 10 miles south-southeast of downtown | [FC27], from a search excerpt |
| Climate | January 31.6 °F, July 73.1 °F, 15.8 in of precipitation a year (CSU's station, 1991 to 2020) | [FC28], from a search excerpt |
| Old town | Exterior changes are reviewed under the Old Town Historic District design standards, so rooftop chillers and condensers are placed early | [FC34], from a search excerpt |

The point is a sample in the old town, not a surveyed site. A pub's peak is
taken as 150 to 250 kW: smaller than the plant of
[DESIGN.md §7.2](../DESIGN.md#72-loads-notional-checked-against-54), with the
same 200 kW boiler.

### 6.2 Electricity

The City Council sets FCU's rates by ordinance, on the advice of its Energy
Board; the PUC has no part [FC2], [FC4]. FCU buys its power from Platte River
Power Authority (PRPA), which four towns own [FC5].

A pub of 50 to 749 kW takes **GS50, schedule E300** (City Code §26-468). The
2026 charges, with the 6 % payment in lieu of taxes included [FC1], [FC2]:

| Component | 2026 |
|---|---|
| Fixed | $33.96 a month |
| Energy | 5.66 ¢ per kWh, all year, at every hour |
| Distribution facilities demand: the month's highest hour, or 70 % of the highest of the last 11 months | $12.76 per kW |
| **Coincident peak demand**: the pub's load in the one hour of the month when PRPA's four towns together peak | **$19.18 per kW in June to September, $15.72 in October to May** |

- **One hour a month sets the largest charge.** FCU cannot say in advance
  which hour it is. It is usually on a weekday between 16:00 and 19:00, in
  the afternoon in summer and the early evening from November to March, and
  for large accounts twelve hours a year set more than 23 % of the annual
  bill [FC1]. FCU flags about ten candidate hours a month by email, and its
  load management page described an **OpenADR 2.0** signal for shedding
  load [FC13], from a search excerpt. A peak load monitor is available on
  request [FC31].
- **No energy by time of day.** Energy costs the same at every hour, so the
  only reason to move a boil is the peak hour and the ratchet: a single
  200 kW hour sets a 140 kW floor on facility demand for 11 months.
- **A summer month**, at 200 kW in the peak hour, 200 kW of facility demand,
  and 15 MWh, comes to about $7,300 (computed).
- **2027:** electricity rises 7.0 %, adopted on 2026-11-17 [FC30]. PRPA
  dropped the 2030 date from its goal of noncarbon power on 2026-10-29
  [FC29].
- **Not verified:** the metering interval for E300 (the code's definitions
  could not be read; city pages give one hour for E300 and 15 minutes for
  E400).

### 6.3 Generation on site

`fc` has no generation, and none is planned. For comparison with the other
sites:

| Rule | What it says | Source |
|---|---|---|
| Net metering | More than 500 W and up to 200 % of the average annual use of the last 24 months; exports on GS50 are credited at 5.34 ¢ per kWh, netted monthly | [FC7], [FC2] §26-468(u) |
| Standards | Interconnection Standards rev. 10 (2022): IEEE 1547-2018 and UL 1741; equipment from makers on the federal Entity List is banned | [FC6] |
| Disconnect | Manual, lockable, visible-break, near the meter, "accessible at all times by FCU personnel" | [FC6] §3.2 |
| Telemetry | At FCU's discretion from 250 kVA of inverters (100 kVA otherwise): an SEL-351 relay with a radio or fiber link, for remote trip, hot-line tag, future dispatch, and real-time power, voltage, frequency, and battery charge | [FC6] §6.2 |

### 6.4 Water

| Item | 2026 | Source |
|---|---|---|
| Volume, May to October | $3.585 per 1,000 gal in the first block, $5.154 above it | [FC1] |
| Volume, November to April | $2.868 and $4.120 per 1,000 gal | [FC1] |
| Meter charge, 1.5 in or 2 in | $156.43 or $235.72 a month | [FC1] |
| **Excess water use** | **$15.59 per 1,000 gal** over the tap's annual allotment (December 1 to November 30) | [FC1], [FC9] |
| The allotment | Set by the water supply requirement paid for the tap; it cannot be moved to another tap | [FC9] |
| A larger tap | Cash in lieu of water rights at $63,800 an acre-foot (about 20 ¢ a gallon), and a plant investment fee of $24,794 (1.5 in) or $48,727 (2 in) | [FC8] |
| Shortage plan | A voluntary Watch, then Levels I to III, which are mandatory; Levels II and III add temporary rate increases (City Code §26-166, §26-167) | [FC10], [FC11] |
| Status | A Water Shortage Watch since 2026-05-01 | [FC11] |

- **The allotment is the exposure.** Water beyond it costs three to five
  times the volume rate, and a brewery's use is mostly process water.
- **2027:** water rises 15.0 % [FC30].

### 6.5 Wastewater

The city treats its own sewage and runs its own pretreatment program, under
City Code chapter 26, article IV [FC3], [FC12], [FC13].

| Item | Value | Source |
|---|---|---|
| Volume, a minor nonresidential user | $4.395 per 1,000 gal on measured flow or the winter-quarter average (December to February), and a meter charge of $58.91 (1.5 in) or $100.81 (2 in) a month | [FC3] |
| Strength, an intermediate user | The same, and a surcharge for each mg/L above a threshold: TSS over 235, BOD over 265, COD over 400, TOC over 130 (about $0.61, $0.51, $0.32, and $0.95 a pound) | [FC3]; pounds computed |
| Significant industrial user | 25,000 gal a day of process wastewater | [FC13], from a search excerpt |
| **Brewery practice** | The city's *Fermented Beverage Best Management Practices* (2023-08-01) are enforceable and ask for a written spill response and trained staff | [FC13], from a search excerpt |
| Grease | Food-service interceptors kept in "continually efficient operating condition" (§26-339); one that fails is inspected quarterly until it passes twice | [FC35], from a search excerpt |

- **The pub discharges about 550 to 1,100 gal a day** (an estimate), far
  under the threshold. Which category of user it is was not read; at
  3,000 mg/L of BOD the intermediate surcharge would add about $11.65 per
  1,000 gal (computed).
- **2027:** wastewater rises 10.0 % [FC30].

### 6.6 Fire, building, and energy rules

| Rule | What it says | Source |
|---|---|---|
| Building codes | The 2024 I-codes with local amendments, for permits submitted from 2026-04-01; the 2023 NEC | [FC14] |
| Fire authority | Poudre Fire Authority (PFA), for Fort Collins, Timnath, and parts of Larimer and Weld counties | [FC15] |
| Fire code | The 2024 IFC, adopted by Ordinance 051, 2025, and amended by Ordinance 201, 2025 (from 2025-12-26) | [FC16], [FC17] |
| **CO₂** | The adoption summary lists no local amendment for CO₂. PFA publishes a CO₂ Beverage Systems Guide (2024), a checklist, and a limited-scope permit, which could not be read. Its notice 2026-03 is on dry ice in walk-in coolers, which bears on the pub's cold room | [FC15]; [FC18], from a search excerpt |
| Building energy | Building Energy and Water Scoring (BEWS): "most commercial and multi-family buildings 5,000 square feet or larger" report energy by June 1. The same page also says 50,000 sq ft, so a pub of 8,000 to 12,000 sq ft is covered on one reading. A performance standard was defeated on 2025-12-02 | [FC19], [FC20] |
| Liquor | The Fort Collins Liquor Licensing Authority; a brew pub serves meals wherever alcohol is sold, and 15 % of its sales are food | [FC21] |
| Food | Larimer County's environmental health department licenses the kitchen: $481 to $581 a year by seats | [FC22] |
| Sales tax | 8.30 % combined: city 4.35 %, Larimer County 1.05 %, state 2.90 % | [FC23] |
| Stormwater | Lot area × $0.00541 × a runoff factor (0.95 for a lot that is nearly all roof and paving) a month: about $51 for 10,000 sq ft | [FC1]; computed |

### 6.7 What the place offers the network

The city's own fiber service sells symmetrical business service at
250 Mbit/s to 1 Gbit/s and enterprise service to 10 Gbit/s; whether it
reaches a given old-town address, and with static addresses, was not
confirmed [FC24]. Cable (Xfinity), fiber from others, fixed wireless, and 5G
fixed wireless also serve the area [FC25], secondary. The plans give `fc`
ShadeCast Cable and Marmot Wireless, which stand for a cable ISP and a
fixed-wireless one; a city fiber network is a third kind of ISP the mock
internet could add.

### 6.8 Not verified

- City Code chapters 12 and 26 on Municode, which refused the request: the
  E300 metering interval, the pretreatment limits for pH and temperature,
  and which category of sewer user a brewpub is.
- PFA's CO₂ guide, checklist, and permit, and therefore any local CO₂
  setpoint.
- The current text of the Fermented Beverage BMPs, which is no longer at its
  address.
- Whether BEWS covers buildings from 5,000 or from 50,000 sq ft.
- Air permitting, which does not arise without a generator.

## 7. Breweries and a pub: licences and records

This section answers decision D18 of [PLANS.md](PLANS.md#9-decisions-needed):
what does a company that is regulated as breweries and as a pub have to keep
at the pub, and what would it do?

### 7.1 One owner cannot hold both Colorado licences

| Rule | What it says | Source |
|---|---|---|
| Brew pub, C.R.S. 44-3-103(5) | "a retail establishment that manufactures not more than one million eight hundred sixty thousand gallons of malt liquor on its licensed premises … each calendar year" | [L5] |
| Food, 44-3-417(4) | "at least fifteen percent of the gross on-premises food and drink income … is from the sale of food" | [L5] |
| Its own wholesale, 44-3-417(2)(a)(IV) | "up to three hundred thousand gallons per calendar year" to retailers; more through an independent wholesaler | [L5] |
| **The bar, 44-3-417(5)(a)** | "It is unlawful for any owner, part owner, shareholder, or person interested directly or indirectly in a brew pub license to … be directly or indirectly interested in any other business licensed pursuant to this article 3 or article 4" | [L5] |
| Its exceptions, 44-3-417(5)(b) | Retail licence classes, other brew pubs among them. The manufacturer's licence and the beer wholesaler's licence are not on the list | [L5] |
| The same bar from the other side, 44-3-402(5)(a) | A manufacturer, or anyone "interested financially in or with a licensed manufacturer", may not be "interested financially, directly or indirectly, in the business of any person licensed to sell at retail" | [L5] |
| A brewery's taproom, 44-3-407(1)(b)(I)(A) | A manufacturer that also holds a wholesaler's beer licence may run "warehouses and one sales room in this state" | [L5], [L6] |

So a company cannot be a licensed brewery at three sites and a licensed brew
pub at the fourth. Real groups use one of three structures:

| Structure | What it means for `fc` | For | Against |
|---|---|---|---|
| P1: the pub belongs to another company | An independent restaurant company holds the brew pub licence and the premises. CherryMountain licenses its name to it and runs the brewing system under contract. Great Divide's Castle Rock pub is reported this way: "Roadhouse will actually lease the space and hold the brewing license, Great Divide will operate the five-barrel brewing system" [L9] | It is a real pub, with a full bar and a kitchen. It explains the site as the plan already has it: the head office runs a brewhouse on a network it does not own. The pub's own records and office belong to the licence holder | A second company in the story. The VPN joins two companies, so what crosses it is a contract matter |
| **P2: every site is a brewery** (recommended) | `fc` holds a manufacturer's and a wholesaler's beer licence like the others, and its taproom is the sales room, with a kitchen under a food licence | One company and one kind of licence, so the design stays as it is: one company's VPN, one company's networks | Legally it is a brewery taproom that serves food, not a brew pub: it pours only the company's own beer. Whether one company may hold a sales room at each of its breweries was not verified |
| P3: every site is a brew pub | All four hold brew pub licences | One company | The production brewery near Aurora would have to be a retail establishment with 15 % of its on-premises income from food, and each site's own wholesale stops at 300,000 gal a year |

The single-site design already licenses `fr` as a manufacturer and wholesaler
with the taproom as its sales room
([DESIGN.md §18A.1](../DESIGN.md#18a1-alcohol-ttb-and-colorado)). P2 extends
that to every site and changes nothing else in the design, which is why it is
the recommendation. P1 is the choice if `fc` must be a brew pub in law: it is
also the richer exercise, because the VPN would then join two companies, but
that boundary is not designed yet. The structure is decision D20.

Either way TTB sees `fc` as a brewery, so what follows holds for P1 and P2.

### 7.2 What must be at the pub

To TTB a brewpub is a brewery: "'tavern' means a portion of brewery premises
where beer is sold to consumers" (27 CFR 25.25(a)).

| Rule | What it requires | Source |
|---|---|---|
| 27 CFR 25.300(a) | "Records required by this part will be prepared and kept by the brewer at the brewery where the operation or transaction occurs and will be available for inspection by any appropriate TTB officer during business hours" | [L1] |
| 27 CFR 25.300(c) | Kept "not less than three years"; TTB may ask for three more | [L1] |
| 27 CFR 25.300(d) | Data on a computer "may be kept at a location other than the brewery if the original transaction (source) records required by §§ 25.292-25.298 are kept available for inspection at the brewery", and it must be "retrievable within five business days" | [L1] |
| 27 CFR 25.291(b)(1) | Daily entries "not later than the close of the business day next succeeding the day on which the transaction occurs" | [L1] |
| 27 CFR 25.25(c) | The tax on beer sold in the tavern is determined "each time beer is added to a tax-determination tank", by "a suitable method for measurement of the beer, such as a meter or gauge glass", before it is dispensed | [L1] |
| 27 CFR 25.42 | A beer meter's error "may not exceed ±0.5 percent", and its tests are recorded | [L1] |
| Colorado Liquor Rules, Regulation 47-502(A)(1) | "Manufacturers and wholesalers shall maintain upon the licensed premises, and make available for inspection … documents or invoices supporting" the monthly excise reports. A brew pub counts as a manufacturer here | [L6] |
| Regulation 47-700(B) | Books for the current tax year and the three before it; an electronic format is allowed; copies "without unreasonable delay" | [L6] |
| C.R.S. 44-3-503 | A monthly excise return by the 20th, and three years of records | [L5] |
| 40 CFR 403.12(o)(2) | Three years of wastewater monitoring records, "available for inspection and copying" | [L11] |
| 29 CFR 1910.146(e)(6) | Each canceled confined-space permit for a year | [L14] |

Nothing in 27 CFR Part 25, the Colorado Liquor Code, or the Liquor Rules says
who may program or operate brewing equipment, or from where. No rule asks for
a historian or an engineering workstation.

### 7.3 What such a company would do

**The rules decide half of it.** The pub's records are made and kept at the
pub. At a brewpub the serving tanks are the tax-determination tanks, so each
transfer into one is a taxable event that must be measured and written down
there. If the only copy of that measurement were in a historian at the head
office, a tunnel outage would be a hole in a required record. So the pub keeps
its own store, and a copy goes to the head office.

**Practice decides the other half.** A plant historian is unusual at a pub
this size; a panel with trends and a brew log are the norm, and a 10-barrel
pub in a vendor's case study has a PLC and a panel and no more [L21]. Edge
software that keeps a month of history on site and forwards it when the link
returns is the usual middle way [L22]. Nobody puts a controls engineer at a
pub. The guidance on reaching it from outside:

| Source | What it says |
|---|---|
| CISA, FBI, EPA, and DOE, 2025-05-06 [L16] | "If remote access is essential, upgrade to a private IP network connection … and use virtual private network (VPN) functionality with a strong password and phishing-resistant multifactor authentication". "Practice and maintain the ability to operate OT systems manually" |
| NIST SP 800-82r3 §6.2.10 [L18] | "Remote access should be provided only if justified and limited to what is required for the business need." Consider "initiating the connection from the OT environment" and "labeling remote connection devices so that operations may disconnect quickly" |
| NCSC-UK with CISA, 2026-01 [L19] | "use brokered connections through a secure gateway located in a separate, security-controlled segment"; "where possible, use just-in-time access"; plan for "the ability to switch from remote support to having to physically attend the site" |
| CISA and partners, 2024-05 [L17] | "Log remote logins to HMIs"; "Create backups of the engineering logic, configurations, and firmware" |

**The answer for D18:**

| Question | Answer | Why |
|---|---|---|
| A historian at the pub? | **Yes, a small one.** It keeps the three-year records at the pub and forwards a copy to the fleet historian | 27 CFR 25.300(a), 25.25(c); Regulation 47-502(A)(1) |
| An engineering workstation at the pub? | **No.** The pub's PLCs are programmed from the head office | No rule asks for one, and nobody at the pub would use it |
| How the head office gets in | Through its jump host, with multifactor sign-in and a recorded session, to its own engineering workstation, and from there to the pub's PLCs through one firewall rule that the pub's edge keeps closed outside a maintenance window | The 2025 and 2026 guidance above |
| What the pub must still be able to do alone | Brew, serve, and record with the tunnel down; hold a copy of each PLC program on site | The same guidance |

**Not verified:** TTB has no sentence that says "one Brewer's Notice for each
site"; it follows from 25.11, 25.22, and 25.62(b). The text of IEC 62443-3-3
and 62443-2-4 was not read (only the requirement titles). How the common
brewery record-keeping services behave with no internet was not found in
vendor documents. The 2026 session's changes to the Liquor Code were checked
only as two bills, both of which failed.

## 8. What the places change in the design

Most of what differs between the four cities is a price or a permit, and it
stays in this document. These differences reach the plant, the network, or
the company, and so belong in the range.

| # | Difference | `fr` | `ep` | `cs` | `fc` | What the range does with it |
|---|---|---|---|---|---|---|
| R1 | **Bulk CO₂ shutoff** | Fail-closed at 30,000 ppm (Denver's policy) | Not required (the 2021 IFC as written) | **At 5,000 ppm**, or on loss of power (the city's amendment) | Not known: the 2024 IFC's adoption lists no local amendment, and PFA's CO₂ guide could not be read | The shutoff point becomes a setting of each site, written by the generator, not a constant in `plc-utilities`. `ep` keeps the company's standard, `fr`'s 30,000 ppm: a company applies one standard where the code asks for less. `fc` keeps it too until PFA's guide is read. At `cs` the shutoff trips at the first alarm tier, so a CO₂ alarm there stops carbonation and the draft system at once |
| R2 | **What the electric bill rewards** | SV-SGT: demand at all hours, energy by time of day | Xcel SG: demand at all hours, and by time of day from the April after the meter is fitted | CSU EIS: almost all demand, by the day; a 68 % ratchet on off-peak demand | FCU E300: the pub's load in PRPA's one peak hour of the month, at $15.72 to $19.18 per kW, flagged by email and an OpenADR signal; energy at one price at every hour; a 70 % ratchet on facility demand | The tariff becomes a setting of each site's power federate. Only `fr` and `ep` have a power area, so only their bills are simulated. At `cs` and `fc` the tariff is documentation until a revenue meter is added. At `fc` the peak-hour signal is the one utility message that could reach the plant: an OpenADR client that holds the boiler off would be a new flow from the internet into the pub, left for later |
| R3 | **A utility link for the DER** | DNP3 over IPsec (the design's teaching device) | The same; Xcel "may require" monitoring at this size | None: CSU asks for telemetry only from 500 kW, and the site has no generation | None | Unchanged. The link stays at `fr` and `ep`, and the documents say it is an assumption at this size, not a rule |
| R4 | **Water in a shortage** | Denver Water (not researched here) | Stage 1 since 2026-04-06; the utility "may require" large non-recoverable users to cut use; an allocation, and a higher price above it | A "preparation" stage since 2026-03-18; at Stage III the utility sets a cut in indoor industrial use, at Stage IV up to 20 % | An annual allotment and $15.59 per 1,000 gal above it; a voluntary Watch since 2026-05-01, and Levels II and III raise rates | A scenario event: the utility orders a cut, and the plant must show its water use and what it deferred. The utilities federate already counts water; the KPIs gain a use against allocation |
| R5 | **Sewer** | Metro's rules; pH under 5.0 prohibited | The same rules; the bill is set by the winter quarter's water | CSU's code; pH under 5.5 or at 12.5 and over prohibited, with a continuous recorder or approved sampling | The city's own program: strength surcharges above set thresholds, and enforceable brewery practices with a written spill response; its pH limits were not read | The plant's discharge permissive (pH 6.0 to 9.0) is inside every limit, so the logic does not change. At `cs` the historian's pH record is the recorder the code asks for |
| R6 | **Records and licences** | Brewery and wholesaler, with the taproom as its sales room | The same | The same | A brewery's records at the site, whatever the licence ([section 7](#7-breweries-and-a-pub-licences-and-records)) | `fc` keeps a small historian. The licence structure is decision D20 |
| R7 | **Long outages** | Not researched | Not researched | CSU's wildfire settings leave a faulted line off until it is patrolled, "four to eight hours" | Not researched | A reason for the standby generator at `cs` that [PLANS.md §4.1](PLANS.md#41-what-each-site-is) leaves for later, and a scenario event: a long outage at a site with no generator |

**What does not reach the design.** Connection fees, sales tax, stormwater
fees, building-energy reporting (only Fort Collins's may cover a site of
this size, and it asks for an annual report, not a limit), and
liquor-licensing procedure. They are here for whoever
writes an exercise's backstory.

**The weather.** Each site's federation takes its own weather file and
elevation ([PLANS.md §4.7](PLANS.md#47-simulation)): Denver's TMY3 file for
`fr`; Aurora Buckley Field (724695) or Denver International (725650) for
`ep`; Colorado Springs (724660) for `cs`; Fort Collins AWOS (724769) for `fc`. Water boils
between about 201 °F at `cs` and 203 °F at `fc`, so the kettle model's
pressure constant changes by less than a degree.

## 9. References

Read on 2026-11-23 unless an entry says otherwise. *Secondary* marks a copy, a summary, or the press.

### Aurora and the eastern plains

- [EP1] Xcel Energy, Colorado electric rates summary, effective 2026-10-19, from <https://www.xcelenergy.com/company/rates_and_regulations/rates/rate_books>.
- [EP2] Public Service Company of Colorado, *Colo. PUC No. 8 Electric* (the tariff book; file dated 2026-11-11): sheets 6, 43 to 43B, 44, 45, 49A, 51, 110, 112, 112A, 118, 124A, R232, <https://xcelnew.my.salesforce.com/sfc/p/1U0000011ttV/a/8b000002Y8xL/kYe61yf.9xyigvh2701Az49XLgU2izDS8ShGaCXiwsQ>.
- [EP3] Xcel Energy, distributed generation technical manual, 2021-10-28, <https://www.xcelenergy.com/staticfiles/xe-responsive/Working%20With%20Us/Renewable%20Developers/DG%20Tech%20Manual_10.28.21_Final.pdf>.
- [EP4] CORE Electric Cooperative, rate schedules, <https://core.coop/my-cooperative/rates-and-regulations/rate-schedules/>.
- [EP5] CORE Electric Cooperative, *Rates and Regulations*, January 2026, <https://core.coop/wp-content/uploads/CORERatesRegulationsJanuary2026.pdf>.
- [EP7] CORE Electric Cooperative, "Rooftop solar", <https://core.coop/energy-resources/rooftop-solar/>.
- [EP8] CORE Electric Cooperative, small generator interconnection guidelines, rev. 6.2, 2025-01-15, <https://core.coop/wp-content/uploads/750_02-SGIG_20250115.pdf>.
- [EP9] CORE Electric Cooperative, standby power connection procedure, <https://core.coop/wp-content/uploads/COREStandbyPowerConnectionProcedure.pdf>.
- [EP11] City of Aurora, "Other utilities" and the *Energy Action Plan*, 2021, <https://www.auroragov.org/residents/other_utilities>.
- [EP12] Aurora Water, 2026 rates, <https://www.auroragov.org/residents/water/pay_my_water_bill/rates>.
- [EP13] Aurora Water, 2026 fee schedule, <https://www.auroragov.org/UserFiles/Servers/Server_1881137/File/Residents/Water/PDFs/Billing/2026/Water%20Fees%20Schedule%202026_accessible%20(1).pdf>.
- [EP14] City of Aurora, *Large Water Users Guide* (appendix F), 2026-01, <https://www.auroragov.org/UserFiles/Servers/Server_1881137/File/Business%20Services/Development%20Center/Water%20&%20Other%20Utilities/Appendix%20F_Large%20Water%20Users%20Guide.pdf>.
- [EP15] Aurora Water, "Drought", <https://www.auroragov.org/residents/water/drought>.
- [EP16] Aurora Water, *Water Shortage Response Plan*, adopted 2026-03-10, <https://www.auroragov.org/UserFiles/Servers/Server_1881137/File/Residents/Water/PDFs/Water%20Facts%20and%20Reports/Aurora%20Water%20WSRP%20Final_accessible.pdf>.
- [EP17] Aurora Water, "Water allocations", <https://www.auroragov.org/WaterAllocations>.
- [EP18] Aurora Water, rules for the winter-quarter average, 2025, <https://cdnsm5-hosted.civiclive.com/UserFiles/Servers/Server_1881137/File/Residents/Water/PDFs/Billing/2025/2.24.25_Rules%20and%20Regs%20Winter%20Quarter%20Average%20Final.pdf>.
- [EP19] Metro Water Recovery, *Rules and Regulations*, 2026, <https://www.metrowaterrecovery.com/wp-content/uploads/2026/04/2026-Rules-and-Regulations-remediated.pdf>.
- [EP20] Metro Water Recovery, "Industrial pretreatment", <https://www.metrowaterrecovery.com/doing-business/industrial-pretreatment/>.
- [EP21] Metro Water Recovery, "Our service area", <https://www.metrowaterrecovery.com/about-us/our-service-area/>.
- [EP22] City of Aurora, adopted building codes (form A245, 2023), <https://cdnsm5-hosted.civiclive.com/Userfiles/DBFiles/server_1881137/202308/19736881/adopted_building_codes.pdf>.
- [EP23] Aurora Fire Rescue, operational permits, <https://www.auroragov.org/residents/public_safety/fire_rescue/life_safety_and_fire_prevention/aurora_fire_operational_permits_information>.
- [EP24] Home Builders Association of Metro Denver, 2024 I-code tracker, 2026-01-20 (secondary), <https://www.hbadenver.com/assets/pdf/HBA+2024+I-Code+Tracker01-20-2026>.
- [EP25] City of Thornton, CO₂ system requirements under the 2021 IFC (secondary for the code text), <https://www.thorntonco.gov/media/file/co2-system-requirements>.
- [EP26] C.R.S. 25-7-142 (a secondary copy), <https://colorado.public.law/statutes/crs_25-7-142>; HB25-1269, <https://leg.colorado.gov/bills/hb25-1269>.
- [EP27] C.R.S. 40-9.5-118 (a secondary copy), <https://colorado.public.law/statutes/crs_40-9.5-118>.
- [EP28] 4 CCR 723-3, Rules 3664, 3853, 3859 (copies at Cornell LII), <https://www.law.cornell.edu/regulations/colorado/4-CCR-723-3-3853>.
- [EP29] City of Aurora, sales tax, occupational privilege tax, and liquor licensing pages, <https://www.auroragov.org/business_services/taxes/sales_tax>, <https://www.auroragov.org/liq>.
- [EP30] NREL, *Users Manual for TMY3 Data Sets*, NREL/TP-581-43156, 2008, <https://docs.nlr.gov/docs/fy08osti/43156.pdf>.
- [EP31] USGS elevation point query and the Census geocoder, <https://epqs.nationalmap.gov/v1/json>, <https://geocoding.geo.census.gov/geocoder/geographies/coordinates>.
- [EP32] City of Aurora, avigation easement packet, 2026, <https://cdnsm5-hosted.civiclive.com/UserFiles/Servers/Server_1881137/File/Departments/Planning/Avigation%20Easement%20for%20DEN%20and%20General%20Aviation%20Airports.pdf>.
- [EP33] Press (secondary): Sentinel Colorado on 2027 rates, 2026-09-14, <https://sentinelcolorado.com/metro/aurora-water-proposes-3-5-residential-rate-hike-for-2027-6-5-bump-for-sewer-rates/>; Denver7 on the evaporative cooling ban, <https://www.denver7.com/news/local-news/aurora-bans-evaporative-cooling-in-new-buildings-requires-sprinklers-off-by-october-through-april>.

### Colorado Springs

- [CS2] Colorado Springs Utilities, *Electric Rate Schedules* (City Council Volume No. 6), <https://www.csu.org/hubfs/Document-Library/ElectricTariff.pdf>; the business rate sheet as of 2026-10-01, <https://www.csu.org/hubfs/Document-Library/BusinessRateSheet.pdf>.
- [CS3] CSU, July 2026 net metering rate case, <https://www.csu.org/hubfs/Document-Library/July-2026-Net-Metering-Rate-Case.pdf>.
- [CS4] CSU, "Net metering changes", <https://www.csu.org/solar-energy-program/net-metering-changes>.
- [CS6] CSU, *Electric Line Extension and Service Standards*, 2026, chapter 13 and §3.14, <https://www.csu.org/hubfs/39606065/Document%20Library/ELCStandardBook.pdf>.
- [CS8] CSU, *Utilities Rules and Regulations*, <https://www.csu.org/hubfs/Document-Library/UtilitiesRulesRegsTariff.pdf>.
- [CS10] CSU, *Water Rate Schedules*, <https://www.csu.org/hubfs/Document-Library/WaterTariff.pdf>.
- [CS11] CSU, *Wastewater Rate Schedules*, <https://www.csu.org/hubfs/Document-Library/WastewaterTariff.pdf>.
- [CS12] CSU, development charges, <https://www.csu.org/hubfs/Document-Library/DevelopmentCharges.pdf>.
- [CS13] CSU, "Water Wise Rules", <https://www.csu.org/water-wise-rules/>.
- [CS14] City of Colorado Springs, water shortage ordinance (City Code 12.4.1301 to 1309), <https://www.csu.org/hubfs/Document-Library/WaterShortageOrdinance.pdf>.
- [CS15] CSU, "Water shortage preparation", <https://www.csu.org/water-service/sustainable-water-plan/water-shortage-preparation>.
- [CS16] CSU, "Significant industrial users", <https://www.csu.org/industrial-pretreatment/significant-industrial-users>.
- [CS17] CSU, Wastewater Discharge Questionnaire, <https://www.csu.org/hubfs/Document%20Library/WastewaterDischargeQuestionnaire.pdf>.
- [CS18] CSU, *Fats, Oils and Grease Policies and Procedures*, 2018, <https://www.csu.org/hubfs/Document-Library/FOGPoliciesProcedures.pdf>.
- [CS19] A copy of City Code chapter 12, article 5 attached to a resolution of the Falcon Area Water and Wastewater Authority (secondary), <https://www.fawwa.net/files/48dcd496d/FAWWA+-+Resolution+Adopting+Wastewater+Discharge+Regulations-c.pdf>.
- [CS20] CSU, "About", <https://www.csu.org/about/>.
- [CS21] CSU, "Utilities Board", <https://www.csu.org/about/leadership/utilities-board>.
- [CS22] CSU, "Southwest Power Pool" and "Five-year rate case", <https://www.csu.org/electric-service/system/southwest-power-pool>, <https://www.csu.org/about/five-year-rate-case>.
- [CS23] City of Colorado Springs, "Stormwater fee", <https://coloradosprings.gov/stormwater-fee>.
- [CS24] Pikes Peak Regional Building Department, code changes, <https://www.pprbd.org/Information/CodeChanges>.
- [CS26] City of Colorado Springs, Ordinance 23-14 (the fire code and its amendments), <https://coloradosprings.gov/system/files/2023-04/Signed%20Ordinance%20No.%2023-14.pdf>.
- [CS27] Colorado Springs Fire Department, fire code amendments and administrative rulings, <https://coloradosprings.gov/fire-department/page/fire-code-amendments-and-administrative-rulings>.
- [CS28] Colorado Springs Fire Department, carbon dioxide systems, <https://coloradosprings.gov/node/75406>.
- [CS29] Colorado Springs Fire Department, CO₂ inspection checklist, 2023-09-14, <https://coloradosprings.gov/system/files/2023-09/co2_checklist.pdf>.
- [CS30] CSU, "Building performance", <https://www.csu.org/business-efficiency/building-perfomance>.
- [CS31] Colorado Regulation 28, 5 CCR 1001-32, <https://www.coloradosos.gov/CCR/GenerateRulePdf.do?ruleVersionId=11123&fileName=5%20CCR%201001-32>.
- [CS33] City of Colorado Springs, liquor rules and regulations, 2026-09-28, <https://coloradosprings.gov/system/files/2026-10/2026%20City%20Liquor%20Rules%20and%20Regulations%20%282026-09-28%29.pdf>.
- [CS35] El Paso County Public Health, retail food establishments, <https://www.elpasocountyhealth.org/service/retail-food-establishments>.
- [CS36] Colorado Department of Revenue, DR 1002, January 2026, <https://spl.cde.state.co.us/artemis/revserials/rev914internet/rev914202601internet.pdf>.
- [CS37] City of Colorado Springs, media page (elevation), <https://coloradosprings.gov/Media>.
- [CS38] EnergyPlus weather data, TMY3 station 724660, Colorado Springs, <https://energyplus-weather.s3.amazonaws.com/north_and_central_america_wmo_region_4/USA/CO/USA_CO_Colorado.Springs-Peterson.Field.724660_TMY3/USA_CO_Colorado.Springs-Peterson.Field.724660_TMY3.stat>.
- [CS41] CSU, "Enhanced powerline safety settings", <https://www.csu.org/safety/wildfire-mitigation/epss>.

### Fort Collins

- [FC1] City of Fort Collins, commercial utility rates (2026), <https://www.fortcollins.gov/Services/Utilities/Commercial-Utilities/Commercial-Rates>.
- [FC2] City of Fort Collins, Ordinance 177, 2025 (electric rates from 2026-01-01), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-17326e57867548b2a4ff9af5ae1f7b4d/ITEM-Attachment-003-831c380a6ef642739063b0e328c13ece.pdf>.
- [FC3] City of Fort Collins, Ordinance 179, 2025 (wastewater rates from 2026-01-01), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-e875fef9dfea41868d5ffc2228e1eb87/ITEM-Attachment-002-b52eebd0eecd4c0ab1dacaf7b5547d05.pdf>.
- [FC4] City of Fort Collins, council item on the 2026 utility rates (the advisory boards), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-e875fef9dfea41868d5ffc2228e1eb87/ITEM-Attachment-005-9530126b588d473289a0899e18796e0f.pdf>.
- [FC5] Platte River Power Authority, *2026 Rate Tariff Schedules* (Tariff FP-26), effective 2026-01-01, <https://prpa.org/wp-content/uploads/2025/12/2026-Rate-Tariff-Schedules_Effective-2026.01.01.pdf>.
- [FC6] Fort Collins Utilities, *Interconnection Standards*, rev. 10, 2022, <https://www.fortcollins.gov/files/sharedassets/city/v/1/utilities/light-amp-power/interconnection_standards.pdf>.
- [FC7] City of Fort Collins, commercial solar, <https://www.fortcollins.gov/Services/Utilities/Commercial-Utilities/Commercial-Solar>.
- [FC8] City of Fort Collins, development fee schedules, <https://www.fortcollins.gov/Business/Building-and-Development/Fee-Schedules>.
- [FC9] City of Fort Collins, commercial water savings (allotments and the excess use surcharge), <https://www.fortcollins.gov/Services/Utilities/Commercial-Utilities/Commercial-Water-Savings>.
- [FC10] City of Fort Collins, water programs (the Water Shortage Action Plan), <https://www.fortcollins.gov/Services/Utilities/Programs-and-Rebates/Water-Programs>.
- [FC11] City of Fort Collins, "Voluntary Water Shortage Watch", 2026, <https://www.fortcollins.gov/News-Articles/News/2026-Q2/Voluntary-Water-Shortage-Watch>.
- [FC12] City of Fort Collins, water and wastewater, <https://www.fortcollins.gov/Services/Utilities/Other-Utilities-Pages/Water>.
- [FC13] Fort Collins Utilities, former fcgov.com pages on industrial pretreatment, significant industrial users, the fermented beverage BMPs, and load management, now redirected; read only in search excerpts.
- [FC14] City of Fort Collins, building code, <https://www.fortcollins.gov/Government/City-Code-and-Charter/Building-Code>.
- [FC15] City of Fort Collins, council item on the adoption of the 2024 codes, <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-1b910f4078744296bbfbf0e195d50b33/ITEM-Attachment-003-b463cb0efd1c40ba8a6f0a257b0ad3e1.pdf>.
- [FC16] City of Fort Collins, Ordinance 201, 2025 (fire code amendments), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-98ce54e66b3d4da38059730c71b0f1c6/ITEM-Attachment-001-e4f1b150f72b49138bbf76556153f144.pdf>.
- [FC17] City of Fort Collins, Ordinance 051, 2025 (the 2024 IFC), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-1b8aaf903396446db23335580b2462a4/ITEM-Attachment-015-e73509dad54847708abedd3433fef1c5.pdf>.
- [FC18] Poudre Fire Authority, permits and fire code adoption pages, <https://www.poudre-fire.org/online-services/apply-for-permits>; refused automated reading, seen only in search excerpts.
- [FC19] City of Fort Collins, Building Energy and Water Scoring, <https://www.fortcollins.gov/Services/Utilities/Commercial-Utilities/Building-Energy-and-Water-Scoring-BEWS>.
- [FC20] City of Fort Collins, Ordinance 223, 2025 (a building performance standard, defeated), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-98ce54e66b3d4da38059730c71b0f1c6/ITEM-Attachment-009-abcf838a30df44c58f0fd3a577b95a1e.pdf>.
- [FC21] City of Fort Collins, liquor licenses, <https://www.fortcollins.gov/Business/Permits-and-Licensing/Liquor-License>.
- [FC22] Larimer County, 2025 environmental health fees, <https://www.larimer.gov/health/environmental-health/2025-environmental-health-fees>.
- [FC23] City of Fort Collins, sales tax, <https://www.fortcollins.gov/Business/Sales-Tax>.
- [FC24] City of Fort Collins, its fiber service's business plans, <https://fcconnexion.com/business>.
- [FC25] BroadbandNow, providers in ZIP 80524 (secondary), <https://broadbandnow.com/Colorado/Fort-Collins?zip=80524>.
- [FC26] USGS elevation point query, <https://epqs.nationalmap.gov/v1/json?x=-105.0765&y=40.5880&units=Feet&wkid=4326&includeDate=true>.
- [FC27] NREL Building Component Library, TMY3 station 724769, <https://bcl.nrel.gov/content/a3f46af0-edc7-0131-8466-48e0eb16a403>; seen in a search excerpt.
- [FC28] Colorado Climate Center, Fort Collins station monthly summary, <https://climate.colostate.edu/pdfs/Wx_Stn_Monthly_Summary_Form_January_2025.pdf>; seen in a search excerpt.
- [FC29] Platte River Power Authority, "Platte River board adopts new strategic plan", 2026-10-29, <https://prpa.org/news-releases/platte-river-board-adopts-new-strategic-plan/>.
- [FC30] City of Fort Collins, proposed and adopted rates for 2027 and 2028, <https://www.fortcollins.gov/News-Articles/News/2026-Q2/Proposed-Rates-202728>.
- [FC31] Fort Collins Utilities, peak load monitor, <https://apps.fcgov.com/peakload/>.
- [FC34] City of Fort Collins, *Old Town Historic District Design Standards*, 2014, <https://www.fcgov.com/planning/pdf/ftc_oldtown_final_july2014_low.pdf>; seen in a search excerpt.
- [FC35] Fort Collins Utilities, public notice on grease interceptors, <https://www.fcgov.com/utilities/img/site_specific/uploads/Public_Notice_Grease_Fine.pdf>; seen in a search excerpt.
- [FC36] City of Fort Collins, Light and Power, <https://www.fortcollins.gov/Services/Utilities/Other-Utilities-Pages/Light-and-Power>.

### Licences, records, and remote access

- [L1] 27 CFR Part 25 (eCFR, as of 2026-11-01), <https://www.ecfr.gov/api/versioner/v1/full/2026-11-01/title-27.xml?part=25>.
- [L5] Colorado Liquor Code, C.R.S. Title 44, Article 3, the Liquor Enforcement Division's 2025 compilation, <https://sbg.colorado.gov/sites/sbg/files/documents/2025_Liquor_Code.pdf>.
- [L6] Colorado Liquor Rules, 1 CCR 203-2, effective 2026-01-01, <https://www.sos.state.co.us/CCR/GenerateRulePdf.do?ruleVersionId=12349&fileName=1%20CCR%20203-2>.
- [L9] Westword, on Great Divide's Castle Rock pub (secondary), <https://www.westword.com/food-drink/great-divide-brewing-celebrates-25th-anniversary-and-will-open-new-castle-rock-brewpub-11387035/>.
- [L11] 40 CFR 403.12, <https://www.ecfr.gov/api/versioner/v1/full/2026-11-01/title-40.xml?part=403&section=403.12>.
- [L14] 29 CFR 1910.146, <https://www.ecfr.gov/api/versioner/v1/full/2026-11-01/title-29.xml?part=1910&section=1910.146>.
- [L16] CISA, FBI, EPA, and DOE, *Primary Mitigations to Reduce Cyber Threats to Operational Technology*, 2025-05-06, <https://www.ic3.gov/CSA/2025/250506.pdf>.
- [L17] CISA and partners, joint fact sheet on defending OT operations, 2024-05-01, <https://www.ic3.gov/CSA/2024/240501.pdf>.
- [L18] NIST SP 800-82 Rev. 3, <https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-82r3.pdf>.
- [L19] NCSC-UK with CISA and partners, *Secure connectivity principles for operational technology*, 2026-01, <https://www.ncsc.gov.uk/files/ncsc-secure-connectivity-for-operational-technology.pdf>.
- [L21] Siemens, a case study of a 10-barrel brewpub (vendor), <https://assets.new.siemens.com/siemens/assets/api/uuid:42cb1ebf-e631-49e9-82e4-aff26ae6d628/JRH-Case-Study-final.pdf>.
- [L22] Inductive Automation, "Edge architectures" (vendor), <https://docs.inductiveautomation.com/docs/8.3/system-architectures/edge-architectures>.
