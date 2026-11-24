# CherryMountain Brewing: the four sites' utilities, prices, and rules

> Status: this file is research for the `fleet` variant of
> [PLANS.md](PLANS.md). We read the primary sources on 2026-11-23 unless a row
> says otherwise. Nothing here is built. The company is notional, which
> means that it does not exist. The places, the utilities, the prices, and the
> rules are real, and each figure carries its source.
>
> Summary: the four sites are on Colorado's Front Range. They differ more by
> their utilities and city rules than by their weather. `fr` and `ep` share
> an electric utility and a sewer district. `cs` and `fc` each buy their power,
> water, and sewer service from a utility that the city owns. Three differences
> reach the plant's control logic. The first is the point at which the bulk CO₂
> supply shuts off. The second is how each bill rewards a boiler that stays off
> the evening peak. The third is the water and sewer rules that a drought or a
> pretreatment program can impose. Pretreatment is the treatment of wastewater
> before it enters the sewer. A fourth difference reaches the company itself:
> Colorado does not let the same owners hold a brewery license and a brew pub
> license.

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

- Sources: each table row names its source in brackets. For example, `[EP12]`
  is item 12 of the Eastern Plains list in [section 9](#9-references). A
  source that is marked *secondary* is a copy, a summary, or a press report.
  We used it because we were not able to read the primary source, and the row
  says so.
- Computed: the word "computed" marks our arithmetic on cited values.
- Not verified: a list with this name closes each site's section. Several
  websites that publish codes refused automated reading. They are the sites
  of Aurora, of Colorado Springs, of Fort Collins on Municode, of Poudre Fire
  Authority, and of the ICC. For that reason, we read some rules through the
  city's own pages, a copy, or a search engine's excerpt of the page. The row
  says which one.
- Sizes: the bills and the fees are for the plant of
  [DESIGN.md §3.1](../DESIGN.md#31-site-and-capacity) and
  [§7.2](../DESIGN.md#72-loads-notional-checked-against-54). That plant has a
  peak of about 250 to 300 kW from a 200 kW electric boiler. Its load factor
  is 14 to 17 %. The load factor is the average load divided by the peak
  load. The plant uses roughly 1,000 to 1,700 gallons of water a day. `fc` is
  smaller.

## 2. The four places side by side

| | `fr` Front Range | `ep` Eastern Plains | `cs` Colorado Springs | `fc` Fort Collins |
|---|---|---|---|---|
| Stands for | Denver, the jurisdiction of [DESIGN.md](../DESIGN.md) | Aurora's east side, near I-70 and E-470 | Colorado Springs | Fort Collins, the old town |
| County | Denver | Adams | El Paso | Larimer |
| Elevation, and the temperature at which water boils | about 5,200 ft, and about 202 °F | 5,514 ft, and about 202 °F | 6,035 ft, and about 201 °F | 4,983 ft, and about 203 °F |
| Weather file (TMY3) | Denver | Aurora Buckley Field (724695) | Colorado Springs (724660) | Fort Collins AWOS (724769) |
| Electric utility, and who sets the rates | Xcel Energy, and the PUC | Xcel Energy, and the PUC | Colorado Springs Utilities, and the City Council as its board | Fort Collins Utilities, and the City Council, by ordinance |
| Schedule | SV-SGT, the design's notional blend | SG | EIS | GS50 (E300) |
| What the electric bill rewards | Low demand at every hour, and energy use outside the evening window. Demand is the highest power draw in a billing period | Low demand at every hour. The bill charges demand by time of day from the April after the site gets its meter | Low on-peak demand by the day (17:00 to 21:00), and a 68 % ratchet off-peak. A ratchet is a minimum billed demand that an earlier peak sets | A low load in PRPA's one peak hour of the month, and a 70 % ratchet |
| Water | Denver Water | Aurora Water: an allocation, and a higher price above it. Stage 1 drought since 2026-04-06 | CSU. Shortage preparation since 2026-03-18. Cuts in indoor industrial use at Stages III and IV | FCU: an annual allotment, and $15.59 per 1,000 gal above it. A voluntary Watch since 2026-05-01 |
| Sewer and pretreatment | Metro Water Recovery, which prohibits a pH under 5.0 | Aurora's sewers, with Metro's treatment and rules. The winter quarter sets the bill | CSU, which prohibits a pH under 5.5 and requires a recorder | The city's own, with enforceable brewery practices |
| Fire authority, and bulk CO₂ shutoff | Denver Fire Department, at 30,000 ppm | Aurora Fire Rescue, none required | Colorado Springs Fire Department, at 5,000 ppm or loss of power | Poudre Fire Authority, none required (the 2024 IFC as written) |
| Codes | The same as [DESIGN.md §18A](../DESIGN.md#18a-regulatory-and-standards-context) | 2021 I-codes | 2021 IFC, and the 2023 regional building code | 2024 I-codes, from 2026-04-01 |
| Sales tax, combined | Not researched | 8.50 % | 8.20 % | 8.30 % |

`fr` is the same as in [DESIGN.md](../DESIGN.md).
[Section 3](#3-fr-front-range-denver) adds two notes on its tariff. For each of
the other three sites, the site's section gives the source of each row.

## 3. `fr`: Front Range (Denver)

The flagship stays the same as in [DESIGN.md](../DESIGN.md). Denver stands for
its jurisdiction. That jurisdiction has Xcel Energy for electricity, Denver
Water, and Metro Water Recovery for treatment and pretreatment. It also has
the Denver Fire Department's CO₂ policy and Energize Denver
([DESIGN.md §7.4](../DESIGN.md#74-utility-tariff-from-open-data),
[§18A](../DESIGN.md#18a-regulatory-and-standards-context)).

The regional research adds two things:

- `ep` has the same electric utility. Xcel's rates summary of 2026-10-19
  gives the flat demand charge of Schedule SG for generation and
  transmission. With riders, the charge is $26.63 per kW in summer and $19.45
  in winter. Without riders, it is $15.14 and $9.11 [EP1], [EP2]. A rider is
  a charge that the utility adds to the base rate. The notional tariff SV-SGT
  of DESIGN.md §7.4 charges $27.14 and $19.79, which is close to the totals
  with riders. But §7.4 says that its values are "without riders or
  adjustments". One of the two statements is wrong. It is possible that URDB
  records the totals. Before you reuse the tariff, compare it with the URDB
  record.
- The time-of-day window moved. Xcel charges SG's demand by time of day from
  16:00 to 21:00 in summer and 17:00 to 21:00 in winter (sheet 43B, from
  2025-11-01) [EP2]. SV-SGT uses 17:00 to 21:00 all year, which comes from
  the small-commercial SG-TOU. A brew day that ends its boils by 15:30 is
  outside both windows.

## 4. `ep`: Eastern Plains, near Aurora

This site is the production brewery. It is on the industrial land of Aurora's
east side, near I-70 and E-470. It has the same electric utility and the same
sewer district as the Front Range site. Its water utility is very different.

### 4.1 The place

| Item | Value | Source |
|---|---|---|
| County | Adams, inside the Aurora city limits (the city also reaches into Arapahoe and Douglas) | [EP31] |
| Elevation | 5,514 ft at I-70 and E-470, and 5,449 to 5,468 ft at three other points nearby | [EP31] |
| Water boils at | about 202 °F (94.4 °C), computed for 1,670 m | computed |
| Weather file | TMY3 "Aurora Buckley Field ANGB", station 724695 (class II data). Denver International, 725650, is the class I alternative | [EP30] |

The points are sample coordinates, not a surveyed site.

### 4.2 Electricity

The I-70 and E-470 area is in the territory of Xcel Energy (Public Service
Company of Colorado). Xcel's tariff lists Aurora as a franchise city. The
city calls Xcel "the main electric and gas utility serving the City of
Aurora" [EP2], [EP11]. CORE Electric Cooperative serves Bennett, which is
further east, and land to the south. We did not find the line between the
two territories at parcel scale.

So this site has the same utility as the Front Range site, and it takes
Schedule SG (Secondary General) [EP1], [EP2]:

| Component | Base rate | With riders, from 2026-10-19 |
|---|---|---|
| Service and facility | $88.07 a month | $105.76 |
| Distribution demand: the greater of the month's peak or half of the peak of the last twelve months | $9.36 per kW | $11.15 |
| Generation and transmission demand, flat: winter, summer | $9.11, $15.14 per kW | $19.45, $26.63 |
| Generation and transmission demand, by time of day: winter, summer | $10.74, $17.86 per kW | $21.39, $29.87 |
| Energy | $0.00853 per kWh | $0.03689 |

- Summer is June 1 to September 30.
- The time-of-day window is 16:00 to 21:00 in summer and 17:00 to 21:00 in
  winter. It applies on weekdays that are not holidays (sheet 43B, from
  2025-11-01).
- A new site starts on flat demand. A customer whose advanced meter arrives
  in 2026 or later moves to demand by time of day. The move comes in the
  billing cycle that holds April 1 of the next year (sheet 43A).
- The other schedules are closed to this plant. SGL excludes net-metered
  customers, who get credit for the power that they export. The SG-TOU pilot,
  SPVTOU, and SG-CPP each need a load factor of 30 % (sheets 44, 45, 49A,
  51). This plant's load factor is 14 to 17 %.
- For the range: `ep` uses the notional tariff of
  [DESIGN.md §7.4](../DESIGN.md#74-utility-tariff-from-open-data), which
  comes from this utility's schedules. The real SG differs from it in two
  ways that matter. Energy costs less than 4 cents a kilowatt-hour, and the
  summer window opens an hour earlier.

If the parcel is in CORE's territory, everything here changes. The schedule
becomes Large Power Service at $165.00 a month, $17.50 per kW, and $0.07224
per kWh in 2026. From 2027-01-01, the figures are $177.79, $18.86, and
$0.07784. The cooperative's board sets the rates [EP4], [EP5].

### 4.3 Generation on site

`ep` has the solar array, the battery, and the generator that `fr` has.

| Rule | Xcel (the PUC's rules) | CORE, for contrast |
|---|---|---|
| Net metering size | Up to 200 % of expected annual consumption, with no kW cap (Schedule NM, sheet 112) [EP2] | 25 kW for a commercial customer (C.R.S. 40-9.5-118(2)(e)(I)) [EP27], [EP5] |
| Credit | One for one, carried forward. At the year's end, the customer gets cash at the average hourly incremental cost, or a dollar credit at the energy rate. The energy rate is about $0.037/kWh on SG (sheet 112A) [EP2] | A 100 kW array is over the cap. CORE pays for exports at avoided cost, $0.04616/kWh [EP7] |
| Battery | Xcel reviews the battery with the array under one agreement. A power control system can limit export (4 CCR 723-3, Rule 3853(c)) [EP28] | No small-generator service for a customer who charges a battery from the grid [EP5] |
| Process | Over 25 kW is Level 2, the fast track. The fee is $475 (sheet R232) [EP2] | Level 2, with a fee of $500 [EP5] |
| Telemetry, the remote reading of measurements | From 25 to 250 kW, Xcel "may require" remote monitoring of real and reactive power, voltage, and status. From 250 kW, it will require the monitoring. From 1 MW, it requires telemetry and a remote disconnect or curtailment (DG manual §8.4, 2021) [EP3] | "All level 2 interconnections will require telemetry" [EP8] |
| Standby generator | An interlocked, open-transition transfer switch makes it a "separate system" [EP3]. Open transition means that the switch disconnects one source first | No parallel operation, and a lockable disconnect near the meter [EP9] |

- The utility's DNP3 link is an assumption. Neither utility's published
  documents name DNP3 for a site this size. Xcel can ask for monitoring at
  100 kW. Its battery program (Schedule AVPP, sheet 124A, from 2026-04-01)
  asks for the interoperability of IEEE 1547-2018 clause 10. The program
  takes batteries up to 750 kW through an aggregator [EP2]. The link of
  [DESIGN.md §7.5](../DESIGN.md#75-utility-der-link) stays a teaching
  device, at `fr` and here.
- The boiler is the lever. After the site moves to demand by time of day,
  the charge in the weekday window is $21 to $30 per kW. The site avoids
  that charge if the 200 kW boiler stays out of the window. A kilowatt-hour
  from the roof is worth less than 4 cents.

### 4.4 Water

Aurora Water prices a commercial customer against an allocation. Before it
connects a large user, it decides whether it wants the load.

| Item | 2026 value | Source |
|---|---|---|
| Tier I, up to the allocation | $7.11 per 1,000 gal | [EP12] |
| Tier II, above the allocation, for the rest of the year | $12.59 per 1,000 gal | [EP12], [EP17] |
| Base charge, 1.5 in or 2 in meter | $39.54 or $57.44 a month | [EP12] |
| Connection fee | $93.29 for each gallon a day of recoverable use, and $130.47 for each gallon a day that does not come back to the sewer | [EP13] |
| Stage 1 surcharge | $2.15 per 1,000 gal on use above 110 % of the winter-quarter average | [EP15] |

- The allocation comes from the figures that the customer gives at the
  planning stage of the site, or from history. Use beyond it "may be
  addressed through monthly bill or payment of additional connection fees"
  [EP13], [EP17].
- The water that leaves in the beer costs the most. Water that goes out in
  the product or up the kettle stack is not recoverable, and its connection
  fee is 40 % higher. At 1,000 to 1,700 gal a day and 70 % recoverable, the
  water connection fee alone is about $104,000 to $178,000 (computed).
- Aurora screens large users. The Large Water Users Guide (appendix F,
  2026-01) sorts a site by gallons per acre per day and by its share of
  non-recoverable use. For a site with 26 to 50 % non-recoverable use, the
  guide allows up to 1,000. It allows 1,001 to 3,000 with notes. A site with
  3,001 to 3,500 needs an exemption, and the guide refuses more. The guide
  adds that "high strength wastewater can impact treatment performance … and
  reduce the recoverability of the water". That statement describes a
  brewery [EP14].
- The city is in a drought stage. The city declared Stage 1 on 2026-04-06,
  and storage was 38 % on 2026-11-12. In Stage 1, the general manager "may
  require large commercial/industrial non-recoverable water users to reduce
  water use". The city allows watering on two days a week [EP15], [EP16].
- Reported, not read in the ordinance: in September 2026, the council banned
  evaporative cooling in new commercial and industrial buildings. The
  proposed 2027 rates are +2 % for water and +6.5 % for sewer [EP33].

### 4.5 Wastewater

Aurora Water owns the sewers. Metro Water Recovery treats the flow and runs
the pretreatment program. It does so under the rules that apply to the Front
Range site ([DESIGN.md §18A.4](../DESIGN.md#18a4-environment)) [EP19], [EP21].

| Item | Value | Source |
|---|---|---|
| Sewer usage | $5.46 per 1,000 gal, on the winter-quarter average (December to February) | [EP12], [EP18] |
| Base charge, 1.5 in or 2 in | $29.73 or $47.58 a month | [EP12] |
| Connection fee, 1.5 in meter | $70,520 to Aurora and $66,770 to Metro | [EP13] |
| Strength | Strength is the concentration of pollutants in the wastewater. Aurora's rate table has no surcharge for BOD (biochemical oxygen demand) or TSS (total suspended solids). Metro prices strength in its connection charge, from the peak month's flow and pounds of BOD, solids, and nitrogen (rule 7.5.1) | [EP12], [EP19] |
| pH | Metro prohibits a pH below 5.0. The rules give no number for the upper limit | Metro rules §6 [EP19] |
| Slug | A slug is a sudden large discharge. The limit is not more than five times the 24-hour average concentration or flow for more than 15 minutes | [EP19] |
| Oil and grease | 200 mg/L, by permit | Rule 6.18.2 [EP19] |
| Significant industrial user | 25,000 gal a day of process wastewater, or by designation | Rules §2, definition 85 [EP19] |
| What to file | Metro's Wastewater Discharge Information Form | [EP20] |

- Winter sets the year's sewer bill. The plant pays sewer charges all year on
  the water that it uses from December to February.
- Metro's rules name "beer and distillery slops, grain processing wastes"
  among the solids that must not go down the drain. The rules also prohibit
  dilution (rule 6.18.5) [EP19].

### 4.6 Fire, building, and energy rules

| Rule | What it says | Source |
|---|---|---|
| Codes | 2021 International Building, Fire, Mechanical, and Energy codes, and the 2023 NEC. The city has no plan to adopt the 2024 codes before about 2027 | [EP22], [EP24] |
| Fire authority | Aurora Fire Rescue | [EP23] |
| CO₂ | We found no local policy, so the 2021 IFC §5307 applies as written. For more than 100 lb, it requires ventilation, or detection with sensors within 12 in of the floor. The detection needs a supervisory alarm at 5,000 ppm and a local alarm at 30,000 ppm. The code asks for no automatic shutoff | [EP25], secondary |
| Building energy | We found no city ordinance. Colorado's program covers buildings of 50,000 sq ft and more. It leaves out a building that is more than half manufacturing (C.R.S. 25-7-142(2)(j)) | [EP26] |
| Stormwater | $12.18 a month for the first 2,500 sq ft of impervious area and $9.60 for each 2,500 sq ft more | [EP12] |
| Airports | The site is in the airport influence district. That district requires a recorded avigation easement before a permit. An avigation easement is a right for aircraft to fly over the land. A crane near Buckley can need 60 days of approval | [EP32] |
| Liquor | Aurora's local licensing authority hears local licenses. The manufacturer's license is the state's. A tasting room is a sales room, and the city can object to it | [EP29] |
| Tax | 8.50 % combined sales tax in the Adams County part (8.00 % in Arapahoe). The city repealed its head tax on 2025-01-01 | [EP29] |

### 4.7 Not verified

- Aurora's City Code and development code: the city's code site refused the
  request. We read the fire code amendments, the drought sections, and the
  sewer sections only through the city's own pages and rate tables.
- The line between Xcel's and CORE's territory at parcel scale.
- The current text of PUC Rule 3664: a copy still says 120 % where Xcel's
  tariff says 200 %.
- Xcel's franchise fee in Aurora, Metro's 2026 connection-charge fractions,
  and the adoption of Aurora's 2027 rates.
- Air permitting and testing rules for the diesel generator.

## 5. `cs`: Colorado Springs

This site is a brewery and taproom that the company bought. One city-owned
utility supplies it with electricity, gas, water, and sewer service.

### 5.1 The place

| Item | Value | Source |
|---|---|---|
| County | El Paso | [CS36] |
| Elevation | 6,035 ft (the city's figure). The weather station is at 6,142 ft | [CS37], [CS38] |
| Standard pressure at the station | 80,767 Pa (11.71 psia). Denver's airport station has 83,011 Pa | [CS38] |
| Water boils at | about 201 °F (93.8 °C), computed from that pressure | computed |
| Weather file | TMY3 "Colorado Springs Muni AP", station 724660 | [CS38] |
| Summer design day | 90.3 °F dry bulb (0.4 %), against 94.3 °F at Denver's airport. The result is a smaller cooling and glycol load | [CS38] |
| Winter design day | −0.8 °F (99.6 %) | [CS38] |
| Climate zone | 5B | [CS38] |

### 5.2 Electricity

Colorado Springs Utilities (CSU) is a municipal utility. The City Council sits
as its Utilities Board and adopts each tariff by resolution. The Colorado PUC
does not set its rates [CS20], [CS21]. CSU joined the Southwest Power Pool's
market on 2026-04-01 [CS22].

A customer whose demand reaches 100 kW and stays under 500 kW takes
Industrial Service, 100 kW Minimum. Its code is EIS, or EIS-P for the
optional schedule, and it is on tariff sheet 7.1 [CS2].

| Component | 2026 | 2027, adopted | Sheet |
|---|---|---|---|
| Access and facilities | $3.7205 a day | $3.9400 a day | 2.7 |
| EIS demand, on-peak | $0.8817 per kW a day | $0.9337 | 2.7 |
| EIS demand, off-peak | $0.5732 per kW a day | $0.6070 | 2.7 |
| EIS base energy | none | none | 2.7 |
| Electric cost adjustment (ECA) | $0.0372/kWh on-peak, $0.0186 off-peak (from 2026-10-01, and it can change monthly) | floats | 2.17 |
| Electric capacity charge (ECC) | $0.0048/kWh | floats | 2.18 |
| EIS-P, the option | A lower demand charge ($0.3485 a kW-day in winter, $0.3777 in summer), and energy by period ($0.0098 to $0.1566/kWh). Critical-peak events cost $0.5849/kWh, with at most 15 a year | higher by 6 % | 2.8, 3.2 |

- On-peak is 17:00 to 21:00 on weekdays, all year, without six holidays
  (sheets 3.1 and 3.2). That is the window of the Front Range tariff, so the
  brew day that ends its boils by 15:30 fits here too.
- Demand is the greatest 15-minute load, raised by 1 % for each 1 % of
  power factor under 95 %. Off-peak billing demand is the greater of the
  off-peak maximum or 68 % of the twelve-month maximum, less the on-peak
  billing demand (sheet 3). The site pays for one bad brew day all year.
- The bill is nearly all demand. At a 300 kW peak in a 30-day month, EIS
  demand is about $7,900 when the boiler runs on-peak. It is about $6,100
  when the site holds on-peak demand to 100 kW (computed from the sheet).
- Base rates rise in 2027 by 6.5 % for electricity and water, 9 % for
  wastewater, and 4 % for gas. The rises are part of a five-year rate case
  that is already adopted [CS22].

### 5.3 Generation on site

| Rule | What it says | Source |
|---|---|---|
| Net metering cap | 150 kW AC for a commercial or industrial customer. A larger system is "subject to approval" | Tariff sheet 20 [CS2] |
| Sizing | 120 % of annual consumption today, and 200 % from 2027-04-01 | [CS3], [CS4] |
| Rate while net-metered | EIS and EIS-P are closed to net-metered customers until 2027-04-01. On that date, industrial net-metered customers move to EIS | Sheets 7.1 and 3.6 [CS2], [CS3] |
| Export | Monthly one-for-one netting. CSU pays for the yearly excess at $0.0195/kWh on-peak and $0.0180 off-peak | Sheets 20.1, 2.21 [CS2] |
| Telemetry | CSU requires a remote terminal unit with status, analog, and control points only at 500 kW AC and more. From 150 kW to under 500 kW, it requires an interval meter | Line Extension and Service Standards §13.02(c), §13.08 [CS6] |
| Disconnect | The disconnect is the customer's. It is visibly open, lockable by the utility, outside, and within 10 ft of the meters | §13.02(a), §13.05(j) [CS6] |
| Standby generator, open transition | "No further coordination is required with Utilities" | §13.03(a) [CS6] |
| Standby generator, closed transition | An operating agreement, relay settings 60 days ahead, and a backup trip within 200 ms | §13.03(b), §13.04 [CS6] |
| Reclosing | The customer must "protect their own sensitive electrical equipment that can be harmed by rapid reclosing" | §3.14 [CS6] |

- No utility link at this size. A 100 kW array here needs no telemetry and
  no utility control point. The utility's DNP3 link at `fr` and `ep` has no
  counterpart at `cs`, whatever the site installs later.
- Solar is worth little on EIS. EIS has no base energy charge, so a
  kilowatt-hour from the roof saves only the ECA and ECC, about 2 to 4 cents.
  The exception is a kilowatt-hour that also lowers billed demand (an
  inference from the tariff).
- Outages can be long. In high fire risk, CSU's lines trip faster. They stay
  off until a crew patrols them, which takes "four to eight hours" [CS41].
  That is the local reason for the standby generator that
  [PLANS.md 4.1](PLANS.md#41-what-each-site-is) leaves for later.

### 5.4 Water

| Item | 2026 | 2027, adopted | Source |
|---|---|---|---|
| Schedule | Nonresidential service, sheet 5 | | [CS10] |
| Service charge, meter under 2 in | $2.0905 a day | $2.2243 | Sheet 2.1 |
| Commodity, November to April | $0.0635 per cubic foot ($8.49 per 1,000 gal) | $0.0676 | Sheet 2.1 |
| Commodity, May to October | $0.0794 per cubic foot ($10.61 per 1,000 gal) | $0.0845 | Sheet 2.1 |
| Development charges, 1.5 in meter | $26,347 and a $19,135 water resource fee. They apply to a bought building only if the meter grows | | [CS8], [CS12] |

- Watering rules hold all year. They allow watering on three days a week at
  most. They allow no spray from 10:00 to 18:00 between May 1 and October 15
  (City Code 12.4.1304) [CS13], [CS14].
- A shortage can reach the brewhouse. In Stage III, "Indoor industrial and
  commercial water use shall be reduced by an amount, to be determined by
  Utilities". In Stage IV, the reduction is up to 20 %, with no new
  connections (12.4.1306 to 1309) [CS14]. The city's "water shortage
  preparation", the step before Stage I, started on 2026-03-18 and continues
  [CS15].

### 5.5 Wastewater

| Item | Value | Source |
|---|---|---|
| Schedule | Nonresidential (SC), billed on metered water | Wastewater tariff sheets 2 and 5 [CS11] |
| Service charge | $1.2104 a day (2027: $1.3193) | Sheet 2 |
| Quantity charge | $0.0380 per cubic foot, $5.08 per 1,000 gal (2027: $0.0414) | Sheet 2 |
| Strength surcharge | On BOD over 280 mg/L and TSS over 270 mg/L, about $0.30 and $0.15 a pound. It applies only to a user over 25,000 gal a day or a significant industrial user | Sheet 9 |
| Consumptive use adjustment | Credits on sub-metered water that does not reach the sewer: 45 % for bottling, 82 % for boiler make-up, 50 % or 100 % for water in the product | Sheets 5 to 5.3 |
| Program | CSU's Industrial Pretreatment Section, under City Code chapter 12, article 5 | [CS16], [CS17] |
| pH | The code prohibits a discharge when the instantaneous pH is "less than 5.5 … or greater than or equal to 12.5". It requires a continuous recorder or approved sampling records | 12.5.702.N, from a copy of the code [CS19] |
| Slug | A doubling of flow for more than 20 minutes, or a pH violation for more than 30 minutes in a row. Report a slug at once, and in writing within 5 days | 12.5.201, 12.5.807 [CS19] |
| Oil and grease | 100 mg/L total | 12.5.702.D [CS19] |
| Significant industrial user | 25,000 gal a day of process wastewater, 5 % of plant capacity, or by designation | 12.5.201 [CS19] |
| What to file | The Wastewater Discharge Questionnaire, within 30 days | [CS17] |
| The taproom's kitchen | A grease interceptor of at least 325 gal, or 1,000 gal with a dishwasher | [CS18] |

The plant discharges about 800 to 1,150 gal a day, which is far under the
threshold. For that reason, CSU does not price strength here unless it
designates the brewery. Instead, the ban on loads that interfere with the
treatment plant controls strength.

### 5.6 Fire, building, and energy rules

| Rule | What it says | Source |
|---|---|---|
| Building code | 2023 Pikes Peak Regional Building Code, based on the 2021 IBC and the 2023 NEC. The Pikes Peak Regional Building Department permits and inspects | [CS24] |
| Fire code | 2021 IFC with local amendments, Ordinance 23-14. The authority is the Colorado Springs Fire Department | [CS26], [CS27] |
| CO₂, permit | An operational permit from 100 lb of CO₂ in an insulated liquid system | Table 105.5.9 as amended [CS26] |
| CO₂, detection | Sensors within 12 in of the floor, and one at each point of use away from the supply. A supervisory alarm at 5,000 ppm and a local alarm at 30,000 ppm | 5307.3.2 as amended [CS26], [CS28] |
| CO₂, shutoff | A fail-safe that "shuts off flow from the supply tank upon reaching 5,000 ppm … and/or loss of power". Without it, the code requires mechanical ventilation | 5307.3.2 item 3, and the department's checklist [CS29] |
| Building energy | We found no city ordinance. Colorado's Regulation 28 covers buildings of 50,000 sq ft and more, so it does not cover this building | [CS30], [CS31] |
| Liquor | The city's municipal judges are the local licensing authority, with the City Clerk as agent | City liquor rules [CS33] |
| Food | El Paso County Public Health licenses the taproom's kitchen | [CS35] |
| Sales tax | 8.20 % combined | [CS36] |

The CO₂ shutoff is the rule that changes a setpoint. Denver's policy shuts
the bulk supply at 30,000 ppm, and `plc-utilities` does that at `fr`.
Colorado Springs asks for the shutoff at 5,000 ppm. Aurora and Fort Collins
ask for none ([6.6](#66-fire-building-and-energy-rules)).

### 5.7 Not verified

- The official text of City Code chapter 12, article 5: the city's code site
  refused the request. The limits above are from a copy attached to another
  authority's resolution and from CSU documents that quote the code.
- The stormwater fee after 2023 ($45 an acre a month in 2023) [CS23].
- The council resolution for the 2026-09-22 net-metering change, and the final
  adoption of the 2027 budget.
- Air permitting for a diesel generator. We did not research it.

## 6. `fc`: Fort Collins

This site is the brewpub, in the old town. As in Colorado Springs, the city
owns its utilities. Fort Collins Utilities (FCU) supplies electricity, water,
wastewater, and stormwater. The city also sells fiber broadband. Gas comes
from Xcel Energy [FC19], [FC36].

### 6.1 The place

| Item | Value | Source |
|---|---|---|
| County | Larimer | [FC22] |
| Elevation | 4,983 ft at 40.5880 N, 105.0765 W, near Old Town Square | [FC26] |
| Water boils at | About 203 °F (95 °C) | Computed |
| Weather file | TMY3 "Fort Collins AWOS" (Fort Collins–Loveland Municipal Airport), station 724769, about 10 miles south-southeast of downtown | [FC27], from a search excerpt |
| Climate | January 31.6 °F, July 73.1 °F, 15.8 in of precipitation a year (CSU's station, 1991 to 2020) | [FC28], from a search excerpt |
| Old town | A review under the Old Town Historic District design standards applies to exterior changes. For this reason, a designer places rooftop chillers and condensers early | [FC34], from a search excerpt |

The point is a sample in the old town, not a surveyed site. This document
takes the peak load of a pub as 150 to 250 kW. That is smaller than the plant
of [DESIGN.md §7.2](../DESIGN.md#72-loads-notional-checked-against-54), with
the same 200 kW boiler.

### 6.2 Electricity

The City Council sets FCU's rates by ordinance, on the advice of its Energy
Board. The PUC has no part [FC2], [FC4]. FCU buys its power from Platte River
Power Authority (PRPA), which four towns own [FC5].

A pub of 50 to 749 kW takes GS50, schedule E300 (City Code §26-468). The
2026 charges include the 6 % payment in lieu of taxes [FC1], [FC2]:

| Component | 2026 |
|---|---|
| Fixed | $33.96 a month |
| Energy | 5.66 ¢ per kWh, all year, at every hour |
| Distribution facilities demand: the highest hour of the month, or 70 % of the highest of the last 11 months | $12.76 per kW |
| Coincident peak demand: the load of the pub in the one hour of the month when PRPA's four towns peak together | $19.18 per kW in June to September, $15.72 in October to May |

- One hour a month sets the largest charge. FCU cannot say in advance which
  hour it is. The hour is usually on a weekday between 16:00 and 19:00. It
  falls in the afternoon in summer and in the early evening from November to
  March. For large accounts, twelve hours a year set more than 23 % of the
  annual bill [FC1].
- FCU flags about ten candidate hours a month by email. Its load management
  page described an OpenADR 2.0 signal to shed load [FC13], from a search
  excerpt. A peak load monitor is available on request [FC31].
- The energy price does not change by time of day. Energy costs the same at
  every hour. So the only reasons to move a boil are the peak hour and the
  ratchet. The ratchet is a floor that an earlier peak sets on later bills. A
  single 200 kW hour sets a 140 kW floor on facility demand for 11 months.
- A summer month comes to about $7,300 (computed). The computation uses
  200 kW in the peak hour, 200 kW of facility demand, and 15 MWh.
- In 2027, electricity rises 7.0 %. The city adopted this rise on 2026-11-17
  [FC30]. On 2026-10-29, PRPA dropped the 2030 date from its goal of
  noncarbon power [FC29].
- Not known: the metering interval for E300. The research was not able to
  read the definitions of the code. City pages give one hour for E300 and
  15 minutes for E400.

### 6.3 Generation on site

`fc` has no generation, and the plans add none. The rules follow, for
comparison with the other sites:

| Rule | What it says | Source |
|---|---|---|
| Net metering | The size is more than 500 W and up to 200 % of the average annual use of the last 24 months. FCU credits exports on GS50 at 5.34 ¢ per kWh and nets them monthly | [FC7], [FC2] §26-468(u) |
| Standards | Interconnection Standards rev. 10 (2022): IEEE 1547-2018 and UL 1741. The standards ban equipment from makers on the federal Entity List | [FC6] |
| Disconnect | Manual, lockable, visible-break, near the meter, "accessible at all times by FCU personnel" | [FC6] §3.2 |
| Telemetry | FCU can require telemetry from 250 kVA of inverters (100 kVA otherwise). The telemetry is an SEL-351 relay with a radio or fiber link. The relay is for remote trip, hot-line tag, future dispatch, and real-time power, voltage, frequency, and battery charge | [FC6] §6.2 |

### 6.4 Water

| Item | 2026 | Source |
|---|---|---|
| Volume, May to October | $3.585 per 1,000 gal in the first block, $5.154 above it | [FC1] |
| Volume, November to April | $2.868 and $4.120 per 1,000 gal | [FC1] |
| Meter charge, 1.5 in or 2 in | $156.43 or $235.72 a month | [FC1] |
| Excess water use | $15.59 per 1,000 gal over the annual allotment of the tap (December 1 to November 30). The tap is the connection of the site to the water main | [FC1], [FC9] |
| The allotment | The water supply requirement paid for the tap sets the allotment. The allotment cannot move to another tap | [FC9] |
| A larger tap | It costs cash in lieu of water rights at $63,800 an acre-foot (about 20 ¢ a gallon). It also costs a plant investment fee of $24,794 (1.5 in) or $48,727 (2 in) | [FC8] |
| Shortage plan | A voluntary Watch, then Levels I to III, which are mandatory. Levels II and III add temporary rate increases (City Code §26-166, §26-167) | [FC10], [FC11] |
| Status | A Water Shortage Watch since 2026-05-01 | [FC11] |

- The allotment is the exposure. Water beyond it costs three to five times
  the volume rate, and a brewery's use is mostly process water.
- In 2027, water rises 15.0 % [FC30].

### 6.5 Wastewater

The city treats its own sewage and runs its own pretreatment program, under
City Code chapter 26, article IV [FC3], [FC12], [FC13].

| Item | Value | Source |
|---|---|---|
| Volume, a minor nonresidential user | $4.395 per 1,000 gal on measured flow or on the winter-quarter average (December to February). The meter charge is $58.91 (1.5 in) or $100.81 (2 in) a month | [FC3] |
| Strength, an intermediate user | The same charges, and a surcharge for each mg/L above a threshold. The thresholds are TSS over 235, BOD over 265, COD (chemical oxygen demand) over 400, and TOC (total organic carbon) over 130. The surcharges are about $0.61, $0.51, $0.32, and $0.95 a pound | [FC3]. This document computed the price per pound |
| Significant industrial user | 25,000 gal a day of process wastewater | [FC13], from a search excerpt |
| Brewery practice | The city's *Fermented Beverage Best Management Practices* (2023-08-01) are enforceable and ask for a written spill response and trained staff | [FC13], from a search excerpt |
| Grease | Food-service interceptors must stay in "continually efficient operating condition" (§26-339). An interceptor that fails gets a quarterly inspection until it passes twice | [FC35], from a search excerpt |

- The pub discharges about 550 to 1,100 gal a day (an estimate). That is far
  under the threshold for a significant industrial user. The research did
  not read which category of user the pub is. At 3,000 mg/L of BOD, the
  intermediate surcharge adds about $11.65 per 1,000 gal (computed).
- In 2027, wastewater rises 10.0 % [FC30].

### 6.6 Fire, building, and energy rules

| Rule | What it says | Source |
|---|---|---|
| Building codes | The 2024 I-codes with local amendments, for permits submitted from 2026-04-01, and the 2023 NEC | [FC14] |
| Fire authority | Poudre Fire Authority (PFA), for Fort Collins, Timnath, and parts of Larimer and Weld counties | [FC15] |
| Fire code | The 2024 IFC. Ordinance 051, 2025 adopted it, and Ordinance 201, 2025 amended it (from 2025-12-26) | [FC16], [FC17] |
| CO₂, the local rule | None. The research searched Ordinances 051 and 201 of 2025 in full. Neither touches IFC §5307 (carbon dioxide), §916 (gas detection), or §105 (permits). The 2021 adoption had no CO₂ rule either. The 2024 IFC applies as written | [FC17], [FC16] |
| CO₂, permit | An operational permit above 100 lb of CO₂ in an insulated liquid system (Table 105.5.9). Construction permits for the gas system (105.6.3) and the detection system (105.6.11) | [FC37], a copy of the code |
| CO₂, detection | Mechanical ventilation at 1 cfm per sq ft with the room at negative pressure, or gas detection in its place. Gas detection has sensors within 12 in of the floor. It gives "an audible and visible supervisory alarm at a normally attended location" at 5,000 ppm. It gives "an audible and visible alarm within the room" at 30,000 ppm (5307.3.1, 5307.3.2). The code also asks for a trouble signal or standby power on loss of power, and a yearly test (916.5, 916.11) | [FC37], a copy of the code |
| CO₂, shutoff | No automatic shutoff. Section 5307.3 has no shutoff sentence. The only "stop the flow of carbon dioxide" in the code is for plant-growing enrichment systems (5307.4.3.1). The code asks for "manual valves or automatic remotely activated fail-safe emergency shutoff valves" at the source and the point of use (5003.2.2.1). A manual valve meets that rule | [FC37], a copy of the code |
| CO₂, PFA's own documents | PFA lists a "CO2 Beverage Systems Guide 2024", a checklist, and a limited-scope permit under gas detection systems. The research was not able to read any of them, so a permit condition beyond the code is possible. The January 2024 guideline of Loveland, the neighboring city, repeats the code and asks only for marked manual shutoffs | [FC18], from a search excerpt, and [FC38] |
| Building energy | Building Energy and Water Scoring (BEWS): "most commercial and multi-family buildings 5,000 square feet or larger" report energy by June 1. The same page also says 50,000 sq ft. So on one reading, BEWS covers a pub of 8,000 to 12,000 sq ft. A performance standard failed to pass on 2025-12-02 | [FC19], [FC20] |
| Liquor | The Fort Collins Liquor Licensing Authority licenses a brew pub, with a hearing. The taproom of a brewery needs only notice to the city ([7.6](#76-fort-collins-a-brew-pub-licence-against-a-sales-room)) | [FC21], [L29] |
| Food | Larimer County's environmental health department licenses the kitchen: $481 to $581 a year by the number of seats | [FC22] |
| Sales tax | 8.30 % combined: city 4.35 %, Larimer County 1.05 %, state 2.90 % | [FC23] |
| Stormwater | Each month: lot area × $0.00541 × a runoff factor (0.95 for a lot that is nearly all roof and paving). That is about $51 for 10,000 sq ft | [FC1]. This document computed the example |

### 6.7 What the place offers the network

The city's own fiber service sells symmetrical business service (the same
speed in both directions) at 250 Mbit/s to 1 Gbit/s. It sells enterprise
service to 10 Gbit/s. The research did not find out whether the service
reaches a given old-town address, or whether it gives static addresses there
[FC24]. Cable (Xfinity), fiber from others, fixed wireless, and 5G fixed
wireless also serve the area [FC25], secondary. The plans give `fc`
ShadeCast Cable and Marmot Wireless, which stand for a cable ISP and a
fixed-wireless ISP. A city fiber network is a third kind of ISP that the mock
internet can add.

### 6.8 Not verified

- City Code chapters 12 and 26 on Municode, which refused the request. The
  open points there are the E300 metering interval, the pretreatment limits
  for pH and temperature, and the category of sewer user for a brewpub.
- PFA's CO₂ guide, checklist, and permit. The open point is whether they add
  an automatic shutoff, or a sensor at each point of use, as a condition of
  the permit. Nothing that the research found suggests it. PFA's site does
  not allow automated reading. A person can open its "Operational Permits"
  page and read the guide, or ask PFA's fire prevention office one question.
  Does PFA require an automatic shutoff valve on a bulk CO₂ beverage system,
  and at what concentration?
- Whether PFA treats the process CO₂ of a brewery (for purging and
  carbonating) as "beverage dispensing". If it does not, the general rule
  applies (5307.2 and 916.8). That rule asks for ventilation, or for
  detection. The detection alarms at half of the 40,000 ppm IDLH, unless the
  fire official sets another value. IDLH is the level that is immediately
  dangerous to life or health.
- The text of the 2024 IFC from ICC itself. The research read it in a copy
  [FC37].
- The current text of the Fermented Beverage BMPs, which is no longer at its
  address.
- Whether BEWS covers buildings from 5,000 or from 50,000 sq ft.
- Air permitting, which does not arise without a generator.

## 7. Breweries and a pub: licences and records

This section answers two decisions of [PLANS.md](PLANS.md#9-decisions-needed).
Decision D20 asks how a company with three breweries and a pub gets its
licenses, and what changes if the pub serves food. Decision D18 asks what
such a company must keep at the pub, and what it does in practice. The
section is background for a notional company, not legal advice. A second pass
on 2026-11-23 read the 2026 edition of the statute, the Liquor Rules, the
state's license data, and the federal rules. The second pass corrected the
first pass in the marked places.

The short answers:

- One set of owners cannot hold a brewery license and a brew pub license.
  Four sections say so. The ban is on owners and shareholders, so a parent
  company or a sister LLC does not avoid it.
- A company can have a taproom at each of its breweries. Each brewery site
  holds its own pair of licenses, and each pair carries one sales room. The
  first pass left this open. The state's own data settle it.
- If the pub pours only beer, food changes nothing. The Liquor Code neither
  requires nor forbids food in a brewery's taproom. The kitchen needs a
  county food license.
- Wine, cider, or spirits change everything. A brewery's taproom can sell
  malt liquor only. A full bar needs a brew pub license at every site, or a
  bar that belongs to an unrelated company.
- To the federal government, the question does not exist. A brewpub is a
  brewery with a "tavern" on its premises, with or without food.

### 7.1 One owner cannot hold both Colorado licences

| Section | Operative words | Source |
|---|---|---|
| Brew pub, C.R.S. 44-3-103(5) | "a retail establishment that manufactures not more than one million eight hundred sixty thousand gallons of malt liquor on its licensed premises or licensed alternating proprietor licensed premises, combined, each calendar year" | [L23] |
| Brewery, 44-3-103(6) | "any establishment where malt liquors are manufactured, except brew pubs licensed under this article 3" | [L23] |
| The ban, from the brew pub's side, 44-3-417(5)(a) | "It is unlawful for any owner, part owner, shareholder, or person interested directly or indirectly in a brew pub license to conduct, own either in whole or in part, or be directly or indirectly interested in any other business licensed pursuant to this article 3 or article 4" | [L23] |
| Its exceptions, 44-3-417(5)(b) | Licenses "described in section 44-3-401 (1)(j) to (1)(t), (1)(v), or (1)(w)", which are retail classes. The classes are beer and wine, hotel and restaurant, tavern, brew pub, club, arts, racetrack, public transportation, and optional premises. The other classes are gaming tavern, vintner's restaurant, distillery pub, and entertainment facility. The manufacturer's license (a) and the wholesalers' licenses (f), (g) are not on the list | [L23] |
| The ban, from the brewery's side, 44-3-402(5)(a) | "It is unlawful for a manufacturer licensed under this article 3 or any person, partnership, association, organization, or corporation interested financially in or with a licensed manufacturer to be interested financially, directly or indirectly, in the business of any person licensed to sell at retail" | [L23] |
| From the wholesaler's side, 44-3-407(2) | The same words for "any licensed wholesaler" | [L23] |
| A brew pub owner and a wholesaler's license, 44-3-308(8) | "It is unlawful for an owner, part owner, shareholder, or person interested directly or indirectly in a brew pub, distillery pub, or vintner's restaurant license to conduct, own in whole or in part, or be directly or indirectly interested in a wholesaler's license" | [L23] |
| The same ban on restaurants and bars | 44-3-413(14) hotel and restaurant, 44-3-414(3) tavern, 44-3-411(3) beer and wine, each with the same list of exceptions | [L23] |
| No help to a retailer, 44-3-308(1)(a) | A manufacturer or wholesaler cannot give a retailer "any financial assistance". It also cannot give "any equipment, fixtures, chattels, or furnishings used in the storing, handling, serving, or dispensing of food or alcohol beverages" | [L23] |

- A brew pub license is a retail license with brewing and some wholesale
  added. The statute calls a brew pub "a retail establishment", and a city
  issues the license (44-3-309(1)(f)). Regulation 47-100(G) counts a brew pub
  among those "licensed to sell at retail". The same rule also counts it as a
  manufacturer and a wholesaler for the rules that bind those [L6], [L23].
- So the owners of a brew pub can hold several brew pubs, and a tavern or a
  restaurant too. They cannot hold a brewery or a wholesaler's license.
- The ban is on people, not on the licensed company. It names "any owner,
  part owner, shareholder, or person interested directly or indirectly". A
  "person" includes a company and its officers (44-3-103(35)). The statute
  sets no minimum share. Regulation 47-304 asks for the history of everyone
  who owns 10 % or more, but that is a disclosure rule [L6].
- No text defines what "interested" means. The rules give three markers:
  - A landlord who receives a fixed rent, or a share of up to 18 % of alcohol
    sales, is not interested (Regulation 47-1107).
  - A person who receives "a fixed amount" for the use of a name is not
    interested. A person who receives "a percentage of sales of alcohol
    beverages shall be considered to be an owner" (47-1108).
  - A court looked at whether a transfer was "a sham transaction" and whether
    the licensee "retained control of or any interest". The case is *Brass
    Monkey v. Louisville City Council*, 870 P.2d 636 (Colo. App. 1994). The
    research read the annotation in [L23], not the opinion.
- The research ran a test on the state's whole list. Of 13,580 licensee names
  in the state's data of 2026-11-10, one holds both a brew pub license and a
  brewery's licenses. Its brewery licenses show an expiry date a short time
  in the past [L24].

### 7.2 A taproom at each brewery

A brewery's taproom is not part of its manufacturer's license. It is the
"sales room" of a second license, the wholesaler's beer license.

| Point | Text | Source |
|---|---|---|
| The brewer needs a wholesaler's license to sell to the public | "All brewers who are licensed pursuant to 44-3-402, C.R.S. and who sell their manufactured product directly to consumers … must also obtain a wholesale license" | Regulation 47-400(A) [L6] |
| The taproom | The purpose of a wholesaler's beer license is "To maintain and operate warehouses and one sales room in this state to handle malt liquors" | 44-3-407(1)(b)(I)(A) [L23] |
| What a sales room does | "sells and serves alcohol beverages for consumption on the licensed premises, sells alcohol beverages in sealed containers for consumption off the licensed premises, or both" | 44-3-103(49) [L23] |
| One for each license, and a license for each place | "Each license issued under this article 3 and article 4 of this title 44 is separate and distinct. … A separate license must be issued for each specific business or business entity and each geographic location" | 44-3-301(3)(a)(I) [L23] |
| Notice to the city, not a city license | The brewer sends the city a copy of its application. The city "must submit its response within forty-five days", on "traffic, noise, or other neighborhood concerns". The city can decide "without requiring a public hearing". The state can deny the application on the city's finding | 44-3-407(1)(b)(II) [L23] |
| No extra fee | "Neither the state or local licensing authority shall impose any additional fees" | Regulation 47-428(J) [L6] |

- The state's data show one company with several breweries and a taproom at
  each. Odell held sales rooms on three separate wholesaler's licenses (Fort
  Collins and two in Denver). Breckenridge Brewery holds two (Littleton and
  its first pub in Breckenridge). Goat Patch holds three around Colorado
  Springs, and Timnath Beerwerks holds one in Timnath and one in Fort Collins
  [L24]. The handbook of the legislature reads the words more narrowly, as
  one sales room in the state [L25]. The licenses that the state issued say
  otherwise.
- A second taproom away from the brewery needs its own wholesaler's beer
  license at that place (Regulation 47-434(E)) [L6].
- Nothing here changed in 2025 or 2026. SB 26-114 (effective 2026-08-12)
  lets a maker of spirits, not a brewer, serve the alcohol of other makers
  with a permit. HB 26-1190 did not pass. It was a bill that "would have
  allowed certain alcoholic beverage license holders to apply for an expanded
  sales room permit" [L23], [L25].

### 7.3 What if the pub serves food

Food alone changes nothing:

- The Code grants food in words to wineries and distillers. The words are
  "may serve and sell food, general merchandise, and nonalcohol beverages"
  (44-3-402(2)(b), (7)(b)). The sales room of the beer wholesaler has no such
  sentence and no ban. Section 44-3-103(11.5) lists "A beer wholesaler that
  operates a sales room" among certain licensees. Those licensees share "an
  outdoor space that is used for food and alcohol beverage service" [L23].
- The Code requires no share of sales from food. The 15 % rule is the brew
  pub's, and the 25 % rule is the restaurant's.
- The kitchen needs a retail food establishment license from the county
  (6 CCR 1010-2). The Liquor Rules ask the licensee to keep it "in full force
  and effect at all times" (Regulation 47-902(B)) [L31], [L6].

What the taproom can pour is the limit:

- A beer wholesaler's sales room exists "to handle malt liquors" and "for the
  purpose of selling malt liquor" (44-3-407(1)(b)(I)(A) and Regulation
  47-412(E)). It cannot sell wine, spirits, or cocktails.
- It cannot sell cider either: "hard cider shall in all respects be treated
  as a vinous liquor except where expressly provided otherwise"
  (44-3-103(20)) [L23].
- Beer from other brewers: the text says "malt liquors", not "of its own
  manufacture" as the winery and distiller clauses say. The research did not
  find out whether the state allows guest beers in practice.

A full bar needs another license, and the owners of a brewery cannot hold it:

| License | What it allows | Food | Can a brewery's owners hold it? |
|---|---|---|---|
| Hotel and restaurant, 44-3-413 | All alcohol by the drink | Meals "not less than twenty-five percent of the gross income from sales of food and drink", over a year | No: 402(5)(a), 407(2), 413(14) |
| Tavern, 44-3-414 | All alcohol by the drink | "sandwiches and light snacks" | No: 414(3) |
| Beer and wine, 44-3-411 | Malt and vinous liquors | Sandwiches and light snacks | No: 411(3) |
| Brew pub, 44-3-417 | All alcohol, and brewing on site | See the next table | Only if they hold no brewery and no wholesaler's license anywhere |

The brew pub license in detail [L23]:

| Point | Text |
|---|---|
| The food rule, 417(4) | "A brew pub licensee shall sell alcohol beverages for on-premises consumption only if at least fifteen percent of the gross on-premises food and drink income of the business of the licensed premises is from the sale of food." Food means meals: "foodstuffs of such nature as is ordinarily consumed by an individual at regular intervals for the purpose of sustenance" |
| How to measure it | The statute gives no period, but the restaurant's rule gives a year. The base is on-premises income, so wholesale is outside it |
| Production | 1,860,000 gallons (60,000 barrels) a year for each brew pub |
| Its own wholesale, 417(2)(a)(IV) | "Sold at wholesale to licensed retailers in an amount up to three hundred thousand gallons per calendar year", about 9,700 barrels |
| Beyond that, 417(2)(a)(II) | "Sold to independent wholesalers for distribution to licensed retailers", with no limit |
| Other alcohol, 417(3)(a) | The brew pub buys it from a licensed wholesaler, and up to $7,000 a year (indexed) from retail stores |

A CherryMountain site makes about 2,500 barrels a year, so neither cap limits
it.

### 7.4 The lawful structures

| Structure | What it allows | What it forbids or costs | Who does it [L24] |
|---|---|---|---|
| P2: every site a brewery (recommended). Each site holds a manufacturer's license and a wholesaler's beer license. The taproom is the sales room. A kitchen operates under a food license | One company. No production cap. The company sells to retailers itself, without limit. Beer by the glass and to go. Food. State licenses only: the city has 45 days to object | Beer only: no wine, cider, or spirits. No interest in any retail license anywhere. In law, `fc` is a brewery with a taproom, not a brew pub | Avery, Goat Patch, Timnath Beerwerks, Odell until September 2026, and Breckenridge Brewery at Littleton and at its first pub |
| P3: every site a brew pub | One company. A full bar and a kitchen at every site. Other retail licenses too (a tavern, a restaurant) | No brewery or wholesaler's license at all (417(5)(a), 308(8)). At least 15 % food wherever people drink alcohol on site. A state license and a city license, with a hearing, for each site. `ep`, a production brewery with no taproom, must become "a retail establishment" with a pub and a kitchen | Left Hand (three brew pubs and a tavern). Denver Beer Co (six brew pubs, its production site among them). Odell (three brew pubs and a vintner's restaurant, issued 2026-09-29). Bristol in Colorado Springs |
| P1: the pub belongs to an unrelated company, which holds a brew pub license. CherryMountain licenses its name and helps | A real brew pub with a full bar. It can buy CherryMountain's packaged beer through CherryMountain's wholesaler's license | No shared owner or shareholder. The name for a fixed fee, never a share of alcohol sales (Regulation 47-1108). No money or serving equipment from CherryMountain (308(1)(a)). The pub company is the brewer: it cannot "allow any other person to exercise the privileges granted under the licensee's license" (301(3)(a)(I)). The pub company must pay for CherryMountain's labor (see the labor rule after this table) | Great Divide Brewery & Roadhouse, Castle Rock: Riverwalk Market LLC holds the brew pub license |
| P1b: the same, with CherryMountain as an alternating proprietor (a second brewer that uses the same premises) | CherryMountain brews its own beer on the pub's premises, under its own license and its own federal notice (44-3-103(3) and Regulation 47-432) | "retail sales of malt liquors shall not be conducted from an area licensed or defined as an alternating proprietor licensed premises". Each keeps "control over their separate business records". "Nothing in this regulation authorizes joint venture operations" | 49 such records in the state's list |
| P4: a CherryMountain brewery, and next door a restaurant that belongs to someone else | CherryMountain keeps P2 at `fc`. The neighbor holds a restaurant or tavern license with a full bar, and buys CherryMountain's beer through a wholesaler's license | The same wall as P1: no shared owner, no help, the name for a fixed fee. Two premises, two companies, two networks | Lyons: Monster Brewing holds the brewery, and Oskar Blues LLC holds the restaurant. The former owners kept the restaurants and licensed the name back [L30] |
| Three breweries and one brew pub, or a restaurant or a tavern, under the same owners | Nothing | Unlawful | No one, on the state's list |

- The labor rule says: "unless the retailer pays the supplier at the normal
  hourly rate of the employee performing the labor, a supplier is prohibited
  from providing to a retailer … any labor". The rule covers any labor beyond
  the delivery and stocking of the supplier's own products. It names labor
  "including … Operating the retailer's powered mechanical equipment"
  (Regulation 47-322(M)(4)) [L6]. Under P1 and P4, the pub is a retailer and
  CherryMountain is its supplier. So if the staff of CherryMountain run the
  brewhouse of the pub or program its controllers, the pub pays for the
  hours.
- Real groups move from P2 to P3. The addresses of Denver Beer Co were sales
  rooms until 2025 and are brew pubs now. Odell changed in September 2026, in
  Fort Collins itself [L24]. The research found no source that it was able to
  open and that gives the reason. The one bill to widen the sales room of a
  brewery failed in 2026.

The recommendation for D20 stands, on firmer ground. P2 is lawful, and it is
what breweries of this size do. It leaves the design as it is: one company,
one VPN, state licenses only. It holds whether or not `fc` has a kitchen. The
design can continue to call `fc` "the brewpub". In law, the site is a brewery
with a taproom and a kitchen that pours beer only.

| If the exercise wants | Choose | What it changes in the design |
|---|---|---|
| The pub as it is, beer and food | P2 | Nothing |
| A full bar at the pub, and one company | P3 | `ep` gains a pub and a kitchen, which it does not have. Every site needs a city license. No network change |
| A full bar at the pub, and a second company in the story | P1 or P4 | The VPN joins two companies. The records of the pub and its small historian belong to the pub company. A historian is a database that stores process values over time. The remote engineering of CherryMountain is billed labor, and the contract terms above belong in the scenario |

### 7.5 Federal law

| Question | Answer | Source |
|---|---|---|
| Is a brewpub a brewery? | Yes. "'tavern' means a portion of brewery premises where beer is sold to consumers. Food, and/or taxpaid wine, and/or taxpaid distilled spirits may also be sold at a tavern operated on brewery premises." | 27 CFR 25.25(a) [L1] |
| One Brewer's Notice for each site? | Yes, from the wording of the law: "The brewery shall consist of the land and buildings described in the brewer's notice. The continuity of the brewery must be unbroken". A brewer with more than one brewery states "the locations of all the breweries" | 26 U.S.C. 5402(a) [L28] and 27 CFR 25.167(b)(3) [L1] |
| Contract brewing | The owner of the premises is the brewer: "only one person, the contract brewer, must qualify as a brewer". So under P1, the pub company holds the notice | TTB Industry Circular 2005-2 [L27] |
| Alternating proprietorship | A second company can brew at the pub under its own notice, as a variance (an approved exception to a rule). The circular says "both the host and the tenant brewer act as brewers and each must be qualified". It also says "the tenant brewer has title to the beer at all stages" | [L27] |
| The federal tied-house rule (a limit on ties between makers and retailers) | It has no effect on P2 or P3. The rule says "Outright ownership of a retail business by an industry member is not an interest which may result in a violation". For malt beverages, it applies only where state law has a similar rule. The rule of Colorado is the one that binds | 27 CFR 6.27(a) and 27 U.S.C. 205 [L26] |
| The reduced tax rate | $3.50 a barrel on the first 60,000 barrels. Four breweries of one company share one allowance: "the brewer shall apportion the 60,000 barrels among the breweries". A pub that brews "under a license, franchise, or other arrangement" shares it too | 26 U.S.C. 5051(a) [L28] and 27 CFR 25.152(c) [L1] |
| Beer between the company's own breweries | It moves without payment of tax | 26 U.S.C. 5414 [L28] and 27 CFR 25.181 [L1] |

At about 10,000 barrels a year in all, the company is far inside every
federal limit.

### 7.6 Fort Collins: a brew pub licence against a sales room

| | Brew pub | A brewery's sales room |
|---|---|---|
| Who licenses | The state and the city: "Colorado uses a dual-authority licensing system" | The state only |
| The city's part | An application, fingerprints and history for each owner of 10 % or more, a posted notice, and a petition from a one-mile radius. Also a hearing where "you must prove … that the neighborhood's needs and desires aren't currently being met by existing businesses" | A copy of the state application, and 45 days to answer on traffic, noise, or neighborhood concerns |
| Time | "120 days or more" | Not stated |
| City fees | $500 application, $75 license, and $1,600 a year in occupation tax | None for the review |
| Food | "Food sales must be at least 15% of gross sales". These are the city's words. The base in the statute is on-premises food and drink income | None required |

Source: the city's liquor license pages and its application guide [L29].
The city's pages do not mention sales rooms.

### 7.7 What must be at the pub

To TTB, a brewpub is a brewery, whatever its state license.

| Rule | What it requires | Source |
|---|---|---|
| 27 CFR 25.300(a) | "Records required by this part will be prepared and kept by the brewer at the brewery where the operation or transaction occurs and will be available for inspection by any appropriate TTB officer during business hours" | [L1] |
| 27 CFR 25.300(c) | The brewer keeps them "not less than three years". TTB can ask for three more | [L1] |
| 27 CFR 25.300(d) | Data on a computer "may be kept at a location other than the brewery if the original transaction (source) records required by §§ 25.292-25.298 are kept available for inspection at the brewery". The data must be "retrievable within five business days" | [L1] |
| 27 CFR 25.291(b)(1) | Daily entries "not later than the close of the business day next succeeding the day on which the transaction occurs" | [L1] |
| 27 CFR 25.25(c) | The brewer determines the tax on beer sold in the tavern "each time beer is added to a tax-determination tank". The brewer uses "a suitable method for measurement of the beer, such as a meter or gauge glass", before the tavern dispenses the beer | [L1] |
| 27 CFR 25.42 | The error of a beer meter "may not exceed ±0.5 percent", and the brewer records its tests | [L1] |
| Colorado Liquor Rules, Regulation 47-502(A)(1) | "Manufacturers and wholesalers shall maintain upon the licensed premises, and make available for inspection … documents or invoices supporting" the monthly excise reports. A brew pub counts as a manufacturer here | [L6] |
| Regulation 47-700(B) | Books for the current tax year and the three before it. The rule allows an electronic format | [L6] |
| C.R.S. 44-3-503, 44-3-701 | A monthly excise return by the 20th, three years of records, and books "open at all times during business hours" | [L23] |
| 40 CFR 403.12(o)(2) | Three years of wastewater monitoring records, "available for inspection and copying" | [L11] |
| 29 CFR 1910.146(e)(6) | Each canceled confined-space permit for a year | [L14] |

The structure changes the owner of the records, not the place of the records:

| Structure | The federal brewer at `fc` | Whose records are at `fc` |
|---|---|---|
| P2 | CherryMountain | CherryMountain's, as a brewery and wholesaler |
| P3 | CherryMountain | CherryMountain's, as a brew pub, with the receipts for alcohol bought at retail and whatever shows the 15 % |
| P1 | The pub company | The pub company's. A copy at CherryMountain is a contract matter |
| P1b | Both | Each keeps its own, and TTB asks the tenant for a variance to keep records at the host's brewery |
| P4 | CherryMountain, at the brewery | The brewery's records stay at the brewery. The restaurant keeps a retailer's books |

Nothing in 27 CFR Part 25, the Colorado Liquor Code, or the Liquor Rules says
who can program or operate brewing equipment, or from where. The one
exception is the labor rule of [7.4](#74-the-lawful-structures). No rule asks
for a historian or an engineering workstation. An engineering workstation is
the computer that programs the controllers of the plant.

### 7.8 What such a company would do

The rules decide half of it. The brewer makes and keeps the records of the
pub at the pub. At a brewpub, the serving tanks are the tax-determination
tanks, so each transfer into one is a taxable event. The brewer must measure
each transfer and write it down there.

Suppose that the only copy of that measurement is in a historian at the head
office. Then an outage of the VPN tunnel makes a hole in a required record.
So the pub keeps its own store, and a copy goes to the head office. This
holds under every structure of [7.4](#74-the-lawful-structures).

Practice decides the other half. A plant historian is unusual at a pub of
this size. A panel with trends and a brew log are the norm. A 10-barrel pub
in a vendor's case study has a PLC (a programmable logic controller) and a
panel, and no more [L21]. The usual middle way is edge software that keeps a
month of history on site and forwards it when the link returns [L22]. Nobody
puts a controls engineer at a pub.

The guidance on access to the pub from outside is:

| Source | What it says |
|---|---|
| CISA, FBI, EPA, and DOE, 2025-05-06 [L16] | "If remote access is essential, upgrade to a private IP network connection … and use virtual private network (VPN) functionality with a strong password and phishing-resistant multifactor authentication". "Practice and maintain the ability to operate OT systems manually" |
| NIST SP 800-82r3 §6.2.10 [L18] | "Remote access should be provided only if justified and limited to what is required for the business need." It also asks the reader to consider "initiating the connection from the OT environment" and "labeling remote connection devices so that operations may disconnect quickly" |
| NCSC-UK with CISA, 2026-01 [L19] | "use brokered connections through a secure gateway located in a separate, security-controlled segment". "where possible, use just-in-time access". The guidance also says to plan for "the ability to switch from remote support to having to physically attend the site" |
| CISA and partners, 2024-05 [L17] | "Log remote logins to HMIs". "Create backups of the engineering logic, configurations, and firmware" |

The answer for D18:

| Question | Answer | Why |
|---|---|---|
| A historian at the pub? | Yes, a small one. It keeps the three-year records at the pub and forwards a copy to the fleet historian | 27 CFR 25.300(a), 25.25(c), and Regulation 47-502(A)(1) |
| An engineering workstation at the pub? | No. The head office programs the PLCs of the pub | No rule asks for one, and nobody at the pub has a use for it |
| How the head office gets in | Through its jump host, with multifactor sign-in and a recorded session, to its own engineering workstation. A jump host is a guarded server that all remote sessions pass through. From the workstation, the head office reaches the PLCs of the pub through one firewall rule. The edge of the pub keeps that rule closed outside a maintenance window | The 2025 and 2026 guidance above |
| What the pub must still be able to do alone | Brew, serve, and record with the tunnel down. Hold a copy of each PLC program on site | The same guidance |

### 7.9 Not verified

- The state's own guidance. The site of the Liquor Enforcement Division did
  not allow automated reading. So the research did not read its charts
  "Liquor Manufacturer Rights" and "Multiple Interests". It also did not read
  any bulletin on sales rooms or on food in a sales room. A binding answer on
  unsettled facts comes from a position statement of the Division and then a
  declaratory order (Regulation 47-200).
- Guest beers in a brewery's taproom: the text allows "malt liquors". The
  research did not find out what the state allows in practice.
- How to measure the 15 % of a brew pub: the statute gives no period.
- The *Brass Monkey* opinion. The research read only the annotation in the
  statute.
- Why groups move to brew pub licenses, and how a brewery above 60,000
  barrels fits under one. The research was not able to open the announcement
  from Odell.
- Chapter 3 of the Fort Collins Municipal Code, and whether the city's
  occupation tax reaches a brewery's sales room.
- One Brewer's Notice for each site is an inference from 26 U.S.C. 5402(a)
  and 27 CFR 25.11, 25.152(c), and 25.167(b)(3). No single sentence says it.
  The research read the U.S. Code in its 2024 edition.
- The text of IEC 62443-3-3 and 62443-2-4. The research read only the
  requirement titles.
- How the common record-keeping services for breweries behave with no
  internet.

## 8. What the places change in the design

Most of what differs between the four cities is a price or a permit, and it
stays in this document. The differences in the table reach the plant, the
network, or the company, and so they belong in the range.

| # | Difference | `fr` | `ep` | `cs` | `fc` | What the range does with it |
|---|---|---|---|---|---|---|
| R1 | Bulk CO₂ shutoff | Fail-closed at 30,000 ppm (Denver's policy) | Not required (the 2021 IFC as written) | At 5,000 ppm, or on loss of power (the city's amendment) | Not required: the 2024 IFC as written, with no local amendment. The research did not read PFA's own guide | The shutoff point becomes a configuration value of each site, not a constant in `plc-utilities`. The generator writes the value. `ep` and `fc` keep the company's standard, which is the 30,000 ppm of `fr`. A company applies one standard where the code asks for less, and 30,000 ppm is also the room alarm of the code. At `cs`, the shutoff trips at the first alarm tier, so a CO₂ alarm there stops carbonation and the draft system at once |
| R2 | What the electric bill rewards | SV-SGT: demand at all hours, energy by time of day | Xcel SG: demand at all hours, and by time of day from the April after Xcel fits the meter | CSU EIS: almost all demand, by the day. A 68 % ratchet on off-peak demand | FCU E300: the load of the pub in the one peak hour of the month for PRPA, at $15.72 to $19.18 per kW. FCU flags the hour by email and an OpenADR signal. Energy at one price at every hour. A 70 % ratchet on facility demand | The tariff becomes a configuration value of the power federate (one program of the co-simulation) of each site. Only `fr` and `ep` have a power area, so the range simulates only their bills. At `cs` and `fc`, the tariff is documentation until the design adds a revenue meter. At `fc`, the peak-hour signal is the one utility message that can reach the plant. An OpenADR client that holds the boiler off is a new flow from the internet into the pub. The design leaves that client for later |
| R3 | A utility link for the DER (distributed energy resource) | DNP3 over IPsec (the design's teaching device) | The same. Xcel "may require" monitoring at this size | None: CSU asks for telemetry only from 500 kW, and the site has no generation | None | Unchanged. The link stays at `fr` and `ep`. The documents say that it is an assumption at this size, not a rule |
| R4 | Water in a shortage | Denver Water (this document did not research it) | Stage 1 since 2026-04-06. The utility "may require" large non-recoverable users to cut use. An allocation, and a higher price above it | A "preparation" stage since 2026-03-18. At Stage III, the utility sets a cut in indoor industrial use. At Stage IV, the cut is up to 20 % | An annual allotment and $15.59 per 1,000 gal above it. A voluntary Watch since 2026-05-01, and Levels II and III raise rates | A scenario event: the utility orders a cut, and the plant must show its water use and what it deferred. The utilities federate already counts water. The KPIs (key performance indicators) gain one for use against allocation |
| R5 | Sewer | Metro's rules. They prohibit a pH under 5.0 | The same rules. The winter quarter's water sets the bill | CSU's code. It prohibits a pH under 5.5 or at 12.5 and over, and asks for a continuous recorder or approved sampling | The city's own program: strength surcharges above set thresholds, and enforceable brewery practices with a written spill response. The research did not read its pH limits | The plant's discharge permissive (pH 6.0 to 9.0) is inside every limit, so the logic does not change. A permissive is a condition that must be true before an action. At `cs`, the pH record of the historian is the recorder that the code asks for |
| R6 | Records and licenses | Brewery and wholesaler, with the taproom as its sales room | The same | The same | A brewery's records at the site, whatever the license ([section 7](#7-breweries-and-a-pub-licences-and-records)) | `fc` keeps a small historian. The license structure is decision D20 |
| R7 | Long outages | Not researched | Not researched | CSU's wildfire configuration leaves a faulted line off until a patrol of the line, "four to eight hours" | Not researched | A reason for the standby generator at `cs` that [PLANS.md §4.1](PLANS.md#41-what-each-site-is) leaves for later. Also a scenario event: a long outage at a site with no generator |

These items do not reach the design: building-energy reporting, connection
fees, sales tax, stormwater fees, and liquor-licensing procedure. Only the
reporting rule of Fort Collins can cover a site of this size, and it asks for
an annual report, not a limit. The items are here for whoever writes the
backstory of an exercise.

The weather: the federation of each site takes its own weather file and
elevation ([PLANS.md §4.7](PLANS.md#47-simulation)). `fr` takes Denver's
TMY3 file. `ep` takes Aurora Buckley Field (724695) or Denver International
(725650). `cs` takes Colorado Springs (724660), and `fc` takes Fort Collins
AWOS (724769). Water boils between about 201 °F at `cs` and 203 °F at `fc`.
So the pressure constant of the kettle model changes by less than a degree.

## 9. References

The research read each entry on 2026-11-23 unless the entry says otherwise. *Secondary* marks a copy, a summary, or the press.

### Aurora and the eastern plains

- [EP1] Xcel Energy, Colorado electric rates summary, effective 2026-10-19, from <https://www.xcelenergy.com/company/rates_and_regulations/rates/rate_books>.
- [EP2] Public Service Company of Colorado, *Colo. PUC No. 8 Electric* (the tariff book, file dated 2026-11-11): sheets 6, 43 to 43B, 44, 45, 49A, 51, 110, 112, 112A, 118, 124A, R232, <https://xcelnew.my.salesforce.com/sfc/p/1U0000011ttV/a/8b000002Y8xL/kYe61yf.9xyigvh2701Az49XLgU2izDS8ShGaCXiwsQ>.
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
- [FC13] Fort Collins Utilities, former fcgov.com pages on industrial pretreatment, significant industrial users, the fermented beverage BMPs, and load management. The pages now redirect. The research read them only in search excerpts.
- [FC14] City of Fort Collins, building code, <https://www.fortcollins.gov/Government/City-Code-and-Charter/Building-Code>.
- [FC15] City of Fort Collins, council item on the adoption of the 2024 codes, <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-1b910f4078744296bbfbf0e195d50b33/ITEM-Attachment-003-b463cb0efd1c40ba8a6f0a257b0ad3e1.pdf>.
- [FC16] City of Fort Collins, Ordinance 201, 2025 (fire code amendments), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-98ce54e66b3d4da38059730c71b0f1c6/ITEM-Attachment-001-e4f1b150f72b49138bbf76556153f144.pdf>.
- [FC17] City of Fort Collins, Ordinance 051, 2025 (the 2024 IFC), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-1b8aaf903396446db23335580b2462a4/ITEM-Attachment-015-e73509dad54847708abedd3433fef1c5.pdf>.
- [FC18] Poudre Fire Authority, permits and fire code adoption pages, <https://www.poudre-fire.org/online-services/apply-for-permits>. The site refused automated reading. The research saw the pages only in search excerpts.
- [FC19] City of Fort Collins, Building Energy and Water Scoring, <https://www.fortcollins.gov/Services/Utilities/Commercial-Utilities/Building-Energy-and-Water-Scoring-BEWS>.
- [FC20] City of Fort Collins, Ordinance 223, 2025 (a building performance standard, defeated), <https://mccmeetingspublic.blob.core.usgovcloudapi.net/fortcollco-meet-98ce54e66b3d4da38059730c71b0f1c6/ITEM-Attachment-009-abcf838a30df44c58f0fd3a577b95a1e.pdf>.
- [FC21] City of Fort Collins, liquor licenses, <https://www.fortcollins.gov/Business/Permits-and-Licensing/Liquor-License>.
- [FC22] Larimer County, 2025 environmental health fees, <https://www.larimer.gov/health/environmental-health/2025-environmental-health-fees>.
- [FC23] City of Fort Collins, sales tax, <https://www.fortcollins.gov/Business/Sales-Tax>.
- [FC24] City of Fort Collins, its fiber service's business plans, <https://fcconnexion.com/business>.
- [FC25] BroadbandNow, providers in ZIP 80524 (secondary), <https://broadbandnow.com/Colorado/Fort-Collins?zip=80524>.
- [FC26] USGS elevation point query, <https://epqs.nationalmap.gov/v1/json?x=-105.0765&y=40.5880&units=Feet&wkid=4326&includeDate=true>.
- [FC27] NREL Building Component Library, TMY3 station 724769, <https://bcl.nrel.gov/content/a3f46af0-edc7-0131-8466-48e0eb16a403>. The research saw this entry in a search excerpt.
- [FC28] Colorado Climate Center, Fort Collins station monthly summary, <https://climate.colostate.edu/pdfs/Wx_Stn_Monthly_Summary_Form_January_2025.pdf>. The research saw this entry in a search excerpt.
- [FC29] Platte River Power Authority, "Platte River board adopts new strategic plan", 2026-10-29, <https://prpa.org/news-releases/platte-river-board-adopts-new-strategic-plan/>.
- [FC30] City of Fort Collins, proposed and adopted rates for 2027 and 2028, <https://www.fortcollins.gov/News-Articles/News/2026-Q2/Proposed-Rates-202728>.
- [FC31] Fort Collins Utilities, peak load monitor, <https://apps.fcgov.com/peakload/>.
- [FC34] City of Fort Collins, *Old Town Historic District Design Standards*, 2014, <https://www.fcgov.com/planning/pdf/ftc_oldtown_final_july2014_low.pdf>. The research saw this entry in a search excerpt.
- [FC35] Fort Collins Utilities, public notice on grease interceptors, <https://www.fcgov.com/utilities/img/site_specific/uploads/Public_Notice_Grease_Fine.pdf>. The research saw this entry in a search excerpt.
- [FC36] City of Fort Collins, Light and Power, <https://www.fortcollins.gov/Services/Utilities/Other-Utilities-Pages/Light-and-Power>.
- [FC37] International Fire Code, 2024 edition, §105.5.9, §105.6, §916, §5003.2.2.1, and §5307. The research read it in the UpCodes copy of Colorado's state fire code (secondary), <https://up.codes/viewer/colorado/ifc-2024/chapter/53/compressed-gases>. The 2021 edition, for comparison, is at <https://up.codes/viewer/colorado/ifc-2021/chapter/53/compressed-gases>.
- [FC38] Loveland Fire Rescue Authority, *Carbon Dioxide Systems for Beverage Dispensing: Plan Review and Reporting Requirements*, January 2024, <https://lfra.org/DocumentCenter/View/330/Carbon-Dioxide-System-for-Beverage-Dispensing-Requirements-PDF>.
- [FC39] Denver Fire Department, policy "IFC 5307", carbon dioxide systems used in beverage dispensing, revised 2017-10-01 (for the comparison), <https://www.denvergov.org/files/assets/public/v/1/fire-department/documents/carbon-dioxide-co2-systems-used-in-beverage-dispensing-applications-policy.pdf>.
- [FC40] NIOSH, *Pocket Guide to Chemical Hazards*, carbon dioxide (TWA 5,000 ppm, short-term 30,000 ppm, IDLH 40,000 ppm), <https://www.cdc.gov/niosh/npg/npgd0103.html>; 29 CFR 1910.1000, Table Z-1 (5,000 ppm), <https://www.govinfo.gov/content/pkg/CFR-2024-title29-vol6/xml/CFR-2024-title29-vol6-sec1910-1000.xml>.

### Licences, records, and remote access

- [L1] 27 CFR Part 25 (eCFR, as of 2026-11-01), <https://www.ecfr.gov/api/versioner/v1/full/2026-11-01/title-27.xml?part=25>.
- [L5] Colorado Liquor Code, C.R.S. Title 44, Article 3, the Liquor Enforcement Division's 2025 compilation (unofficial), <https://sbg.colorado.gov/sites/sbg/files/documents/2025_Liquor_Code.pdf>. The first pass read it, and it matches [L23] in every quoted section.
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
- [L23] Colorado Revised Statutes 2026, Title 44, the Office of Legislative Legal Services' edition, current through the 2026 regular session, <https://olls.info/crs/crs2026-title-44.pdf>.
- [L24] Colorado Liquor Enforcement Division, open data, rows updated 2026-11-10: Liquor Licenses, <https://data.colorado.gov/d/ier5-5ms2>; Sales Rooms, <https://data.colorado.gov/d/9pwz-gi5v>; Recently Approved, <https://data.colorado.gov/d/htyp-tqzh>; Recently Expired and Surrendered, <https://data.colorado.gov/d/pwjb-9dd5>.
- [L25] Colorado Legislative Council Staff, *Colorado Liquor Licensing Handbook*, 2024 (secondary), <https://leg.colorado.gov/sites/default/files/r24-656_colorado_liquor_licensing_handbook.pdf>; summary of major legislation on liquor, 2026, <https://content.leg.colorado.gov/sites/default/files/r26-578-liquor-tobacco-marijuana-gaming-summary-of-major-legislation-2026-accessible.pdf>.
- [L26] 27 CFR Part 6, "Tied house" (eCFR, as of 2026-11-19), <https://www.ecfr.gov/api/versioner/v1/full/2026-11-19/title-27.xml?part=6>; 27 U.S.C. 205, <https://www.govinfo.gov/content/pkg/USCODE-2024-title27/html/USCODE-2024-title27-chap8-subchapI-sec205.htm>.
- [L27] TTB, Industry Circular 2005-2, alternating proprietors at brewery premises, <https://www.ttb.gov/public-information/industry-circulars/archives/2005/05-02>.
- [L28] 26 U.S.C. 5051, 5402, and 5414 (U.S. Code, 2024 edition), <https://www.govinfo.gov/content/pkg/USCODE-2024-title26/html/USCODE-2024-title26-subtitleE-chap51-subchapA-partI-subpartD-sec5051.htm>.
- [L29] City of Fort Collins, liquor licence pages and the new liquor licence application guide, <https://www.fortcollins.gov/Business/Permits-and-Licensing/Liquor-License/Liquor-License-Applications>; Liquor Licensing Authority agenda, 2026-06-17, <https://www.fortcollins.gov/files/sharedassets/city/v/1/city-clerks-office/liquor-license/agendas/june-17-lla-agenda-ada-cover-sheet.pdf>.
- [L30] Westword, on the sale of Oskar Blues and its restaurants (secondary), <https://www.westword.com/food-drink/monster-energy-oskar-blues-sale-13320947/>.
- [L31] Colorado Retail Food Establishment Rules and Regulations, 6 CCR 1010-2.
