// Open the page
await tools.browser_navigate({ url: "https://www.ilanzou.com/s/Kq4lWc35" });
await tools.browser_wait_for({ time: 3 });

// Get the page snapshot
const snap = await tools.browser_snapshot();
text("=== PAGE SNAPSHOT ===");
text(snap);

// Get network requests to see API calls
const net = await tools.browser_network_requests();
text("=== NETWORK REQUESTS ===");
text(net);

// Get console messages 
const console = await tools.browser_console_messages();
text("=== CONSOLE ===");
text(console);
