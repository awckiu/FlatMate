---
name: student-tactile-ui
description: Design principles and UI guidelines for creating bouncy, tactile, student-centric, high-energy interfaces that turn stressful legal and renting tasks into engaging, friendly, and empowering experiences.
---

# Student-Centric Tactile UI Engine ("Campus-Ready Delight")

## 1. Tone & Persona: The "Savvy Older Sibling"
- **Zero Legal Jargon by Default**: Translate clauses into student realities.
  - *Legalese*: "Clause 14.2 imposes joint and several contractual obligations."
  - *Plain English*: "⚠️ House Rule Alert: If Dave doesn't pay his share of the rent, the landlord can legally demand the full balance from you."
- **"Legalese" <-> "No-Cap" Toggle**: Provide an interactive pill toggle on every flag:
  - `Formal Law Mode`: Official statute citations (e.g., *s.1(1) Tenant Fees Act 2019*).
  - `Real Talk Mode`: What this actually means for your weekend party, guest policy, or bedroom deposit.

## 2. Spring Physics & Tactile Bounciness
Never use stiff linear or standard CSS transitions. Use high-personality spring dynamics:
- **Jelly Pop (For positive confirmations, file drops, badge unlocks, flag alerts)**:
  - Spring Config: `{ type: "spring", stiffness: 350, damping: 14, mass: 0.7 }`
- **Micro-Squish (For buttons, pills, toggles)**:
  - While tap/click: `transform: scale(0.95) translateY(1px)`
  - Transition: `cubic-bezier(0.34, 1.56, 0.64, 1)` (snappy overshoot)
- **Fluid Floating Cards**:
  - Cards elevate smoothly on hover with asymmetric warm shadows:
    `box-shadow: 0 12px 28px -6px rgba(40, 98, 78, 0.14), 0 8px 12px -4px rgba(0, 0, 0, 0.04)`
- **List Layout Continuity**:
  - Use Framer Motion `layout` or `layoutId` so that expanding flags or removing bill items smoothly slides other elements with spring physics rather than abrupt layout jumps.

## 3. Playful Gamification & Stress-Relief Widgets
1. **The "Landlord Red-Flag Radar"**:
   - An animated speedometer / radar gauge that ticks up from *"Smooth Sailing ✨"* to *"Major Shady Alert 🚩"* with color-morphing indicators.
2. **Interactive "FairShare" Bill Splitter**:
   - A bouncy drag-and-drop pizza or slider where flatmates adjust room sizes and see exact monthly splits update with live spring layout animations.
3. **Interactive Contract Highlighter**:
   - Clicking any flagged issue smoothly auto-scrolls the document viewer to the clause and triggers an animated marker wipe across the text.
4. **"Deposit Shield" XP Bar**:
   - Turns tedious onboarding into a mini-game:
     - Agreement analyzed: `+50 XP`
     - 4 Room Photos Logged: `+100 XP`
     - Council Tax Exemption Sent: `+150 XP`
     - Status: *"Deposit Master Shield Activated 🛡️"*

## 4. Student Campus Visual Language
- **Color Palette**:
  - Primary Green: `#225E49` (Forest Mint - grounding, trustworthy).
  - Delight Butter: `#FDF6B2` / `#FEF08A` (Warm highlighter yellow).
  - Alert Coral: `#FF5C5C` (Friendly, non-punitive warning).
  - Paper Canvas: `#FBFBF9` (Warm editorial paper feel, avoiding harsh clinical `#FFFFFF`).
- **Tactile Elements**:
  - Rounded pills (`rounded-full`), sticker-style badge rotation (`rotate-[-1.5deg]`), dashed border accents, and handwritten annotations.
- **Sound & Micro-Sensory Feedback (Optional Web Audio API)**:
  - Soft bubble pop on upload complete.
  - Satisfying typewriter click on email copy.

## 5. Verification Checklist for UI Components
1. *"Would a stressed 19-year-old moving into their first shared flat find this approachable, fun, and comforting?"*
2. *"Does every button, toggle, and card respond within 16ms with physical tactile weight?"*
3. *"Are scary legal issues paired with actionable, empowering solutions rather than pure panic?"*
