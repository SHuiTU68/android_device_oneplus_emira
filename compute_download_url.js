const crypto = require('crypto');

const key = "lanZouY-disk-app";
const keyBuffer = Buffer.from(key, 'utf8');

function encryptHex(text) {
    const cipher = crypto.createCipheriv('aes-128-ecb', keyBuffer, null);
    cipher.setAutoPadding(true);
    let encrypted = cipher.update(text, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    return encrypted.toUpperCase();
}

const fileId = "37724484895";
const userId = "6547478";
const shareId = "2nklXzk9";
const uuid = "bp-yA-QhXPSMFNPFkvFhd";

// downloadId = encryptHex(fileId + "|" + userId)
const downloadId = encryptHex(fileId + "|" + userId);
console.log("downloadId:", downloadId);

// enable param
const enable = "&enable=1";

// devType = 6 (PC)
const devType = "6";

// timestamp
const timestamp = Date.now();
const timestampHex = encryptHex(String(timestamp));
console.log("timestamp:", timestamp, "hex:", timestampHex);

// auth = encryptHex(fileId + "|" + timestamp)
const auth = encryptHex(fileId + "|" + timestamp);
console.log("auth:", auth);

// Construct the download URL
const downloadUrl = "https://apis.ilanzou.com/unproved/file/redirect" +
    "?downloadId=" + downloadId +
    enable +
    "&devType=" + devType +
    "&uuid=" + uuid +
    "&timestamp=" + timestampHex +
    "&auth=" + auth +
    "&shareId=" + shareId;

console.log("\nDownload URL:");
console.log(downloadUrl);