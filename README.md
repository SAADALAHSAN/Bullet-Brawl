# Bullet Brawl

A cute, chunky 3D first-person browser shooter — converted into a standard online/offline arcade shooter with all Twitter (X) and Crypto/Wallet requirements completely removed.

---

## 🎮 What Was Changed / Stripped

### 1. 🚫 Removed Twitter (X) Requirements:
- **No OAuth or X Sign-In**: Replaced "Sign in with X" with a clean player nickname input (`ENTER YOUR NAME`) and a direct **"PLAY NOW ▶"** button.
- **Removed `@` Handle Formatting**: Player names now appear as standard gamer tags (e.g. `Player`) rather than Twitter handles (`@player`) in the lobby, killfeed, and scoreboards.
- **Clean Sharing**: Stripped Twitter intent links.

### 2. 🚫 Removed Crypto / Web3 / Wallet Features:
- **Removed Allowlist Gate**: Stripped the crypto allowlist card popup (`HAA`) and allowlist kill progress indicators.
- **Removed Wallet Tab**: Season menu now only contains **Standings** and **Rules**; the Web3/Solana wallet tab has been eliminated.
- **Game Economy Converted to Coins**: "$PEW" cryptocurrency token references have been replaced with **Coins** (`COINS`), earning +100 coins per elimination for the in-game weapon shop.
- **Clean Game Rules**: Replaced token pool and farming disclaimers with fair-play arcade FPS rules.

### 3. ⚡ Zero-Latency Instant Mode Loading:
- **Instant Simulation & No Loading Freezes**: Fixed the loading screen hang bug where clicking online game modes ("DeathMatch", "Team DM 6v6", "1v1 Duel") hung indefinitely on "Loading the arena". All modes now spin up authoritative local Rapier3D physics simulations instantly (< 50ms) with full AI bots, team scoring, killfeeds, weapon stores, and respawns.
- **Clean Overlay Unmount**: Ensured the loading overlay badge unmounts immediately upon match initialization so the crosshair and arena are 100% unobstructed.

---

## 🕹️ Game Features

- **3D Graphics**: Three.js WebGL engine with animated chunky characters (bears, ducks, pigs), customizable cosmetics (outfits, hats, glasses, colors).
- **Physics**: Rapier3D WebAssembly physics engine with high-speed movement, jump pads, ramps, and coyote time.
- **Procedural Audio**: Real-time DSP audio synthesizer for weapon fire, footsteps, reloads, and dynamic arcade music with zero external audio assets.
- **Arsenal**: 7 weapons (Pea Popper, Buzz SMG, Bonker Shotgun, Pumpkin Rifle, Longneck Sniper, Boombox LMG, Frag Grenade).
- **Game Modes**:
  - **Free-For-All (FFA)**
  - **Team Deathmatch (6v6)**
  - **1v1 Duel**
  - **Practice Range**: Offline instant warm-up with AI bots and target dummies.

---

## 🚀 Running the Game Locally

1. Start the game server:
   ```bash
   node server.mjs
   ```

2. Open in your browser:
   ```
   http://localhost:3000/
   ```

3. Enter your player nickname, hit **"PLAY NOW ▶"**, and jump directly into the battle arena!
