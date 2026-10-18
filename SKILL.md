# Name: Fluid Human-Centric UI Engine (Anti-Slop Animations)
# Description: Enforces fluid motion paths, spring physics, dynamic loading states, and non-generic layouts to eliminate an "AI-generated" appearance.

## 1. Animation Physics & Timing Guardrails
* **No Linear Easing:** Never use linear transitions or rigid CSS easings (`ease-in-out`). Always apply realistic spring physics for UI movements.
  - Standard Spring Config: `stiffness: 150, damping: 20, mass: 0.8` (Snappy, professional, organic).
  - Micro-Interaction Config: `stiffness: 400, damping: 30` (For buttons, toggles, and hover states).
* **Avoid Generic Slop Effects:** Ban purple-to-blue neon gradients, 3-card feature grids with geometric accent shapes, and floating abstract background blur vectors. 

## 2. Micro-Interactions & Form Physics
* **Progressive Disclosure:** Multi-step flows (like onboarding or bill setups) must visually pull forward or recede. When transitioning steps, use horizontal layout shifts with content opacity staggering (`delayChildren`).
* **Active Status Metaphors:** Status changes (e.g., Legal Flag Detected, Meter Reading Submitted) must utilize subtle micro-vibrations or color-morphing indicators instead of generic flashing alerts.
* **Layout Layout Continuity:** Always apply Framer Motion's `layoutId` attribute when moving elements between UI lists (like changing active bills or toggling tabs) so elements slide naturally to their new layouts.

## 3. High-Density Context Windows
* **The Loading Narrative:** When the Gemini API parses a tenancy contract, do not use an uninspired spinning wheel. Implement multi-tier skeleton blocks that subtly mimic the shapes of lines of text being drawn in, paired with a changing textual description string (e.g., "Reading statutory terms..." ➔ "Analyzing guest policies...").
* **Interactive Data Surfaces:** Expandable lists, like a breakdown of council tax estimates, must expand vertically using an organic `height: "auto"` transition container to prevent sudden content jumps.

## 4. Verification Step Before Generating UI
1. "Does this component look like a generic tech boilerplate, or does it have distinct personality, asymmetric layout framing, or refined typography?"
2. "Are layout elements transitioning using unified spring dynamics?"
3. "Is there any risk of unexpected layout shifts during asynchronous state mutations?"
