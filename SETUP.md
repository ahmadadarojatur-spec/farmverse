# 🌾 FarmVerse — Panduan Deployment Lengkap

## Struktur File

```
farmverse/
├── farmverse.html          ← Game UI (Telegram Mini App)
├── farmverse_bot.py        ← Backend Telegram Bot (Python)
├── FarmVerse.sol           ← Smart Contract (BNB Chain)
└── SETUP.md                ← Panduan ini
```

---

## 🚀 Langkah 1 — Deploy Smart Contract (BNB Chain)

### Prasyarat
- MetaMask dengan saldo BNB (~0.05 BNB untuk gas)
- Akses ke [Remix IDE](https://remix.ethereum.org)

### Cara Deploy

1. Buka **Remix IDE** → tempel isi `FarmVerse.sol`
2. Compile dengan Solidity **0.8.20**
3. Di tab "Deploy & Run":
   - Environment: **Injected Provider (MetaMask)**
   - Network: **BNB Smart Chain (Chain ID: 56)**
   - Atau gunakan **BSC Testnet (Chain ID: 97)** untuk uji coba
4. Deploy dalam urutan:
   ```
   1. Deploy FarmVerseToken  → catat alamat: 0x...FVT
   2. Deploy FarmVerseNFT    → catat alamat: 0x...NFT
   3. Deploy FarmVerse(FVT_ADDR, NFT_ADDR)
   4. Panggil FarmVerseToken.setMinter(FARMVERSE_ADDR)
   5. Panggil FarmVerseNFT.setMinter(FARMVERSE_ADDR)
   ```
5. Catat semua alamat kontrak!

### Verifikasi Kontrak (opsional tapi direkomendasikan)
- Buka [BscScan](https://bscscan.com) → Verify Contract
- Ini membuat kontrak lebih terpercaya untuk pemain

---

## 🤖 Langkah 2 — Setup Telegram Bot

### Buat Bot Baru
1. Chat dengan [@BotFather](https://t.me/BotFather) di Telegram
2. Kirim `/newbot` → ikuti instruksi
3. Simpan **BOT_TOKEN** yang diberikan
4. Kirim `/setmenubutton` → pilih bot → masukkan URL game

### Konfigurasi `.env`

```bash
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
GAME_URL=https://yourdomain.com/farmverse.html
ADMIN_ID=123456789
CONTRACT_ADDR=0x...FarmVerse contract address
FVT_ADDR=0x...FarmVerseToken address
```

### Instalasi Dependensi

```bash
pip install python-telegram-bot==20.7 aiohttp aiosqlite python-dotenv
```

### Jalankan Bot

```bash
python farmverse_bot.py
```

Untuk production, gunakan **systemd** atau **screen/tmux**:

```bash
# Dengan screen
screen -S farmverse
python farmverse_bot.py

# Detach: Ctrl+A+D
# Reattach: screen -r farmverse
```

---

## 🌐 Langkah 3 — Deploy Game UI (farmverse.html)

### Opsi A — GitHub Pages (Gratis)
1. Buat repo GitHub baru
2. Upload `farmverse.html`
3. Settings → Pages → Source: main / root
4. URL: `https://username.github.io/farmverse/farmverse.html`

### Opsi B — Vercel (Gratis, CDN Cepat)
```bash
npm i -g vercel
vercel deploy farmverse.html
```

### Opsi C — VPS / Server Sendiri
```nginx
# Nginx config
server {
    listen 443 ssl;
    server_name game.farmverse.io;
    
    ssl_certificate     /etc/letsencrypt/.../fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/.../privkey.pem;
    
    root /var/www/farmverse;
    index farmverse.html;
    
    location / {
        try_files $uri $uri/ =404;
        add_header Access-Control-Allow-Origin *;
    }
}
```

> ⚠️ **WAJIB HTTPS!** Telegram Mini App hanya berfungsi di URL HTTPS.

---

## 🔗 Langkah 4 — Integrasi Web3 di Game

Update bagian `connectWallet()` di `farmverse.html` dengan kode nyata:

```javascript
async function connectWallet() {
  if (typeof window.ethereum === 'undefined') {
    showToast('❌ Install MetaMask terlebih dahulu!');
    return;
  }
  try {
    const provider = new ethers.BrowserProvider(window.ethereum);
    await provider.send("eth_requestAccounts", []);
    const signer = await provider.getSigner();
    const addr = await signer.getAddress();
    
    // Switch to BSC
    await window.ethereum.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: '0x38' }], // BSC Mainnet
    });
    
    const bal = await provider.getBalance(addr);
    
    STATE.walletConnected = true;
    STATE.walletAddr = addr;
    STATE.walletBal = parseFloat(ethers.formatEther(bal));
    
    // ... update UI
    document.getElementById('walletDisconnected').style.display = 'none';
    document.getElementById('walletConnected').style.display = 'block';
    document.getElementById('walletAddr').textContent = addr.slice(0,6)+'...'+addr.slice(-4);
    document.getElementById('walletBal').textContent = STATE.walletBal.toFixed(4);
    
    showToast('✅ Wallet terhubung!');
  } catch (err) {
    showToast('❌ Gagal connect: ' + err.message);
  }
}
```

---

## 💰 Model Monetisasi (Pay-to-Win)

| Item                | Harga    | Keuntungan Player       |
|---------------------|----------|------------------------|
| Starter Boost       | 0.005 BNB| 1.5x hasil × 6 jam     |
| Premium Bundle      | 0.02 BNB | 2x hasil × 24 jam      |
| Land NFT            | 0.01 BNB | +4 slot lahan permanen |
| Robot NFT           | 0.05 BNB | Auto-harvest tiap 30m  |
| Ultra VIP Pass      | 0.10 BNB | 3x semua + NFT gratis  |

### Proyeksi Pendapatan (estimasi)
- 100 pemain × Premium Bundle = **2 BNB/hari**
- 50 NFT Land terjual = **0.5 BNB** (sekali)
- 20 Robot NFT = **1 BNB** (sekali)

---

## 🔒 Keamanan

- [ ] Tambahkan rate limiting di bot Python
- [ ] Validasi Telegram `initData` sebelum proses request
- [ ] Gunakan environment variables, jangan hardcode key
- [ ] Audit smart contract sebelum mainnet
- [ ] Aktifkan re-entrancy guard jika perlu
- [ ] Backup database secara berkala

---

## 📱 Test di Telegram

1. Buka [@BotFather](https://t.me/BotFather) → `/setmenubutton`
2. Pilih bot kamu → masukkan `https://your-game-url.com/farmverse.html`
3. Buka bot → tap tombol menu di bawah

---

*FarmVerse — Blockchain Farming Game on Telegram*
*Built with Python + Solidity + Telegram Mini App API*
