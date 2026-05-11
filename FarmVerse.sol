// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * ███████╗ █████╗ ██████╗ ███╗   ███╗██╗   ██╗███████╗██████╗ ███████╗███████╗
 * ██╔════╝██╔══██╗██╔══██╗████╗ ████║██║   ██║██╔════╝██╔══██╗██╔════╝██╔════╝
 * █████╗  ███████║██████╔╝██╔████╔██║██║   ██║█████╗  ██████╔╝███████╗█████╗
 * ██╔══╝  ██╔══██║██╔══██╗██║╚██╔╝██║╚██╗ ██╔╝██╔══╝  ██╔══██╗╚════██║██╔══╝
 * ██║     ██║  ██║██║  ██║██║ ╚═╝ ██║ ╚████╔╝ ███████╗██║  ██║███████║███████╗
 * ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝
 *
 * FarmVerse Smart Contract
 * Network: BNB Chain (BSC)
 * Token: FVT (FarmVerse Token)
 *
 * Features:
 *   - ERC-20 FVT Token (in-game currency on-chain)
 *   - ERC-721 NFT (Land, Tools, Characters)
 *   - Pay-to-Win item purchases
 *   - On-chain harvest recording
 *   - Referral reward distribution
 */

// ─────────────────────────────────────────────────────────
// Minimal ERC20 Interface
// ─────────────────────────────────────────────────────────
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

// ─────────────────────────────────────────────────────────
// FARMVERSE TOKEN (FVT) — ERC-20
// ─────────────────────────────────────────────────────────
contract FarmVerseToken {
    string  public constant name     = "FarmVerse Token";
    string  public constant symbol   = "FVT";
    uint8   public constant decimals = 18;
    uint256 public totalSupply;

    address public owner;
    address public minter; // FarmVerse main contract

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    modifier onlyOwner()  { require(msg.sender == owner,  "Not owner");  _; }
    modifier onlyMinter() { require(msg.sender == minter, "Not minter"); _; }

    constructor() {
        owner  = msg.sender;
        minter = msg.sender;
        // Initial supply: 100M FVT to owner
        _mint(msg.sender, 100_000_000 * 1e18);
    }

    function setMinter(address _minter) external onlyOwner {
        minter = _minter;
    }

    function mint(address to, uint256 amount) external onlyMinter {
        _mint(to, amount);
    }

    function burn(uint256 amount) external {
        require(balanceOf[msg.sender] >= amount, "Insufficient balance");
        balanceOf[msg.sender] -= amount;
        totalSupply -= amount;
        emit Transfer(msg.sender, address(0), amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "Allowance exceeded");
        allowance[from][msg.sender] -= amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(to != address(0), "Zero address");
        require(balanceOf[from] >= amount, "Insufficient balance");
        balanceOf[from] -= amount;
        balanceOf[to]   += amount;
        emit Transfer(from, to, amount);
    }

    function _mint(address to, uint256 amount) internal {
        totalSupply        += amount;
        balanceOf[to]      += amount;
        emit Transfer(address(0), to, amount);
    }
}

// ─────────────────────────────────────────────────────────
// FARMVERSE NFT — ERC-721 (minimal)
// ─────────────────────────────────────────────────────────
contract FarmVerseNFT {
    string  public constant name   = "FarmVerse NFT";
    string  public constant symbol = "FVNFT";

    address public owner;
    address public minter;

    uint256 public nextTokenId = 1;

    mapping(uint256 => address) public ownerOf;
    mapping(address => uint256) public balanceOf;
    mapping(uint256 => address) public getApproved;
    mapping(uint256 => string)  private _tokenURI;
    mapping(uint256 => uint8)   public nftType; // 1=Land, 2=Tool, 3=Robot, 4=Character

    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event Approval(address indexed owner, address indexed approved, uint256 indexed tokenId);

    modifier onlyOwner()  { require(msg.sender == owner,  "Not owner");  _; }
    modifier onlyMinter() { require(msg.sender == minter, "Not minter"); _; }

    constructor() {
        owner  = msg.sender;
        minter = msg.sender;
    }

    function setMinter(address _minter) external onlyOwner { minter = _minter; }

    function mint(address to, uint8 _nftType, string calldata uri) external onlyMinter returns (uint256) {
        uint256 tokenId = nextTokenId++;
        ownerOf[tokenId]   = to;
        balanceOf[to]     += 1;
        nftType[tokenId]   = _nftType;
        _tokenURI[tokenId] = uri;
        emit Transfer(address(0), to, tokenId);
        return tokenId;
    }

    function tokenURI(uint256 tokenId) external view returns (string memory) {
        require(ownerOf[tokenId] != address(0), "Nonexistent token");
        return _tokenURI[tokenId];
    }

    function transferFrom(address from, address to, uint256 tokenId) external {
        require(ownerOf[tokenId] == from, "Not token owner");
        require(msg.sender == from || msg.sender == getApproved[tokenId], "Not authorized");
        ownerOf[tokenId]   = to;
        balanceOf[from]   -= 1;
        balanceOf[to]     += 1;
        delete getApproved[tokenId];
        emit Transfer(from, to, tokenId);
    }
}

// ─────────────────────────────────────────────────────────
// FARMVERSE MAIN CONTRACT
// ─────────────────────────────────────────────────────────
contract FarmVerse {
    // ── State ──────────────────────────────────────────
    address public owner;
    FarmVerseToken public fvt;
    FarmVerseNFT   public nft;

    uint256 public constant PRECISION = 1e18;

    // ── Item Prices (in BNB) ───────────────────────────
    uint256 public price_land_nft      = 0.01  ether;
    uint256 public price_robot_nft     = 0.05  ether;
    uint256 public price_premium_bundle= 0.02  ether;
    uint256 public price_starter_boost = 0.005 ether;
    uint256 public price_ultra_pass    = 0.10  ether;

    // ── FVT Reward per harvest (in FVT, 18 decimals) ──
    uint256 public harvestReward = 100 * PRECISION; // 100 FVT

    // ── Referral ───────────────────────────────────────
    mapping(address => address) public referredBy;
    uint256 public referralRewardFVT = 500 * PRECISION; // 500 FVT

    // ── Player Data ────────────────────────────────────
    struct Player {
        uint256 totalHarvest;
        uint256 level;
        uint256 fvtEarned;
        bool    isPremium;
        uint256 premiumUntil;
        bool    exists;
    }
    mapping(address => Player) public players;
    address[] public allPlayers;

    // ── NFT ownership types ────────────────────────────
    mapping(address => bool) public hasRobotNFT;
    mapping(address => bool) public hasLandNFT;

    // ── Events ─────────────────────────────────────────
    event PlayerRegistered(address indexed player, address indexed referrer);
    event HarvestRecorded(address indexed player, uint256 amount, uint256 fvtRewarded);
    event ItemPurchased(address indexed buyer, string item, uint256 bnbPaid);
    event NFTMinted(address indexed to, uint256 tokenId, uint8 nftType);
    event PremiumActivated(address indexed player, uint256 until);
    event Withdrawal(address indexed to, uint256 amount);

    modifier onlyOwner() { require(msg.sender == owner, "Not owner"); _; }
    modifier registered() { require(players[msg.sender].exists, "Not registered"); _; }

    constructor(address _fvt, address _nft) {
        owner = msg.sender;
        fvt   = FarmVerseToken(_fvt);
        nft   = FarmVerseNFT(_nft);
    }

    // ── Registration ───────────────────────────────────
    function register(address referrer) external {
        require(!players[msg.sender].exists, "Already registered");
        players[msg.sender] = Player({
            totalHarvest: 0,
            level: 1,
            fvtEarned: 0,
            isPremium: false,
            premiumUntil: 0,
            exists: true
        });
        allPlayers.push(msg.sender);

        // Handle referral
        if (referrer != address(0) && referrer != msg.sender && players[referrer].exists) {
            referredBy[msg.sender] = referrer;
            fvt.mint(referrer, referralRewardFVT);
            emit PlayerRegistered(msg.sender, referrer);
        } else {
            emit PlayerRegistered(msg.sender, address(0));
        }

        // Starter FVT
        fvt.mint(msg.sender, 100 * PRECISION);
    }

    // ── Record Harvest (called by backend oracle) ──────
    function recordHarvest(address player, uint256 cropCount) external onlyOwner {
        require(players[player].exists, "Player not found");
        uint256 reward = cropCount * harvestReward;
        players[player].totalHarvest += cropCount;
        players[player].fvtEarned    += reward;

        // Level up every 50 harvests
        players[player].level = (players[player].totalHarvest / 50) + 1;

        fvt.mint(player, reward);
        emit HarvestRecorded(player, cropCount, reward);
    }

    // ── Buy Land NFT ───────────────────────────────────
    function buyLandNFT(string calldata metadataURI) external payable registered {
        require(msg.value >= price_land_nft, "Insufficient BNB");
        uint256 tokenId = nft.mint(msg.sender, 1, metadataURI);
        hasLandNFT[msg.sender] = true;
        emit ItemPurchased(msg.sender, "land_nft", msg.value);
        emit NFTMinted(msg.sender, tokenId, 1);
    }

    // ── Buy Robot NFT ──────────────────────────────────
    function buyRobotNFT(string calldata metadataURI) external payable registered {
        require(msg.value >= price_robot_nft, "Insufficient BNB");
        uint256 tokenId = nft.mint(msg.sender, 3, metadataURI);
        hasRobotNFT[msg.sender] = true;
        emit ItemPurchased(msg.sender, "robot_nft", msg.value);
        emit NFTMinted(msg.sender, tokenId, 3);
    }

    // ── Buy Premium Bundle ─────────────────────────────
    function buyPremiumBundle() external payable registered {
        require(msg.value >= price_premium_bundle, "Insufficient BNB");
        uint256 until = block.timestamp + 24 hours;
        players[msg.sender].isPremium    = true;
        players[msg.sender].premiumUntil = until;
        fvt.mint(msg.sender, 1000 * PRECISION); // 1000 FVT bonus
        emit ItemPurchased(msg.sender, "premium_bundle", msg.value);
        emit PremiumActivated(msg.sender, until);
    }

    // ── Buy Starter Boost ──────────────────────────────
    function buyStarterBoost() external payable registered {
        require(msg.value >= price_starter_boost, "Insufficient BNB");
        uint256 until = block.timestamp + 6 hours;
        players[msg.sender].isPremium    = true;
        players[msg.sender].premiumUntil = until;
        fvt.mint(msg.sender, 200 * PRECISION);
        emit ItemPurchased(msg.sender, "starter_boost", msg.value);
        emit PremiumActivated(msg.sender, until);
    }

    // ── Buy Ultra VIP Pass ─────────────────────────────
    function buyUltraPass(string calldata nftURI) external payable registered {
        require(msg.value >= price_ultra_pass, "Insufficient BNB");
        players[msg.sender].isPremium    = true;
        players[msg.sender].premiumUntil = block.timestamp + 365 days;
        fvt.mint(msg.sender, 10_000 * PRECISION);
        // Bonus NFT
        uint256 tokenId = nft.mint(msg.sender, 4, nftURI);
        emit ItemPurchased(msg.sender, "ultra_pass", msg.value);
        emit NFTMinted(msg.sender, tokenId, 4);
    }

    // ── Check premium status ───────────────────────────
    function isPremiumActive(address player) public view returns (bool) {
        return players[player].isPremium &&
               players[player].premiumUntil > block.timestamp;
    }

    // ── Check robot auto-harvest availability ──────────
    function canAutoHarvest(address player) external view returns (bool) {
        return hasRobotNFT[player] && players[player].exists;
    }

    // ── Leaderboard (top 10) ───────────────────────────
    function getTopPlayers() external view returns (address[] memory, uint256[] memory) {
        uint256 len = allPlayers.length > 10 ? 10 : allPlayers.length;
        address[] memory addrs  = new address[](len);
        uint256[] memory scores = new uint256[](len);

        // Simple copy (production: use off-chain sorting)
        for (uint256 i = 0; i < len; i++) {
            addrs[i]  = allPlayers[i];
            scores[i] = players[allPlayers[i]].totalHarvest;
        }
        return (addrs, scores);
    }

    // ── Owner: update prices ───────────────────────────
    function updatePrices(
        uint256 _land,
        uint256 _robot,
        uint256 _premium,
        uint256 _starter,
        uint256 _ultra
    ) external onlyOwner {
        price_land_nft       = _land;
        price_robot_nft      = _robot;
        price_premium_bundle = _premium;
        price_starter_boost  = _starter;
        price_ultra_pass     = _ultra;
    }

    // ── Owner: withdraw BNB revenue ────────────────────
    function withdraw() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "Nothing to withdraw");
        (bool ok,) = owner.call{value: bal}("");
        require(ok, "Transfer failed");
        emit Withdrawal(owner, bal);
    }

    // ── Owner: transfer ownership ──────────────────────
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        owner = newOwner;
    }

    receive() external payable {}
}
