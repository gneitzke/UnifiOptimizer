#!/usr/bin/env python3
"""
RSSI Diagnostic Tool
Analyzes actual RSSI distribution and helps identify measurement issues
"""

from api.cloudkey_gen2_client import CloudKeyGen2Client
from utils.keychain import get_credentials
from collections import defaultdict

def diagnose_rssi_readings():
    """Analyze RSSI readings in detail to identify potential issues"""
    
    print("🔍 RSSI Diagnostic Tool")
    print("=" * 70)
    
    # Get credentials and connect
    creds = get_credentials('default')
    client = CloudKeyGen2Client(
        creds['host'], 
        creds['username'], 
        creds['password'], 
        site='default', 
        verify_ssl=False
    )
    
    # Get clients
    clients_response = client.get('s/default/stat/sta')
    
    # Handle both dict with 'data' key and direct list
    if isinstance(clients_response, dict):
        clients = clients_response.get('data', [])
    else:
        clients = clients_response if isinstance(clients_response, list) else []
    
    print(f"\n📊 Total clients returned by API: {len(clients)}\n")
    
    # Detailed analysis
    wired_count = 0
    wireless_count = 0
    
    # Track RSSI by device type
    by_manufacturer = defaultdict(list)
    by_connection = defaultdict(list)
    
    # Suspicious patterns
    perfect_rssi = []  # RSSI > -30 (too good to be true)
    no_rssi = []       # Missing or 0 RSSI
    
    print("Analyzing each client...\n")
    print(f"{'Hostname':<25} {'Type':<8} {'RSSI':<8} {'MAC':<20} {'Manufacturer':<20}")
    print("-" * 100)
    
    for client in clients:
        hostname = client.get('hostname', client.get('name', 'Unknown'))[:24]
        mac = client.get('mac', 'Unknown')
        is_wired = client.get('is_wired', False)
        rssi = client.get('rssi', None)
        signal = client.get('signal', None)  # Alternative field
        oui = client.get('oui', 'Unknown')[:19]
        
        # Determine connection type
        if is_wired:
            conn_type = "Wired"
            wired_count += 1
        else:
            conn_type = "Wireless"
            wireless_count += 1
            
            # Use rssi or signal field
            actual_rssi = rssi if rssi is not None else signal
            
            # FIX: Some UniFi controllers return positive RSSI values
            if actual_rssi is not None and actual_rssi > 0:
                actual_rssi = -actual_rssi
            
            if actual_rssi is not None:
                by_manufacturer[oui].append(actual_rssi)
                
                # Check for suspicious values
                if actual_rssi > -30:
                    perfect_rssi.append({
                        'hostname': hostname,
                        'mac': mac,
                        'rssi': actual_rssi,
                        'oui': oui
                    })
                
                rssi_str = f"{actual_rssi} dBm"
            else:
                no_rssi.append({
                    'hostname': hostname,
                    'mac': mac,
                    'oui': oui
                })
                rssi_str = "NO RSSI"
        
        # Print first 20 clients for manual inspection
        if len([c for c in clients if clients.index(c) < clients.index(client)]) < 20:
            rssi_display = rssi_str if not is_wired else "N/A (wired)"
            print(f"{hostname:<25} {conn_type:<8} {rssi_display:<8} {mac:<20} {oui:<20}")
    
    # Summary
    print("\n" + "=" * 70)
    print("📋 SUMMARY")
    print("=" * 70)
    print(f"\n🔌 Connection Types:")
    print(f"   Wired:    {wired_count}")
    print(f"   Wireless: {wireless_count}")
    
    # RSSI distribution
    print(f"\n📶 RSSI Distribution:")
    all_rssi = []
    for rssi_list in by_manufacturer.values():
        all_rssi.extend(rssi_list)
    
    if all_rssi:
        ranges = {
            'Excellent (> -50)': len([r for r in all_rssi if r > -50]),
            'Good (-50 to -60)': len([r for r in all_rssi if -60 < r <= -50]),
            'Fair (-60 to -70)': len([r for r in all_rssi if -70 < r <= -60]),
            'Poor (-70 to -80)': len([r for r in all_rssi if -80 < r <= -70]),
            'Critical (< -80)': len([r for r in all_rssi if r <= -80]),
        }
        
        for label, count in ranges.items():
            percentage = (count / len(all_rssi) * 100) if all_rssi else 0
            bar = '█' * int(percentage / 2)
            print(f"   {label:<22} {bar} {count:3d} ({percentage:5.1f}%)")
        
        print(f"\n   Average RSSI: {sum(all_rssi) / len(all_rssi):.1f} dBm")
        print(f"   Best RSSI:    {max(all_rssi)} dBm")
        print(f"   Worst RSSI:   {min(all_rssi)} dBm")
    
    # Suspicious findings
    if perfect_rssi:
        print(f"\n⚠️  SUSPICIOUS: {len(perfect_rssi)} clients with unrealistically strong signal (> -30 dBm)")
        print("   These might be mesh APs, bridges, or measurement errors:")
        for item in perfect_rssi[:5]:
            print(f"   - {item['hostname']}: {item['rssi']} dBm (MAC: {item['mac']}, OUI: {item['oui']})")
        if len(perfect_rssi) > 5:
            print(f"   ... and {len(perfect_rssi) - 5} more")
    
    if no_rssi:
        print(f"\n⚠️  WARNING: {len(no_rssi)} wireless clients missing RSSI data")
        for item in no_rssi[:5]:
            print(f"   - {item['hostname']} (MAC: {item['mac']}, OUI: {item['oui']})")
        if len(no_rssi) > 5:
            print(f"   ... and {len(no_rssi) - 5} more")
    
    # By manufacturer
    print(f"\n🏭 Top Manufacturers by Client Count:")
    sorted_mfg = sorted(by_manufacturer.items(), key=lambda x: len(x[1]), reverse=True)
    for mfg, rssi_list in sorted_mfg[:10]:
        avg_rssi = sum(rssi_list) / len(rssi_list)
        print(f"   {mfg:<30} {len(rssi_list):3d} clients (avg: {avg_rssi:5.1f} dBm)")
    
    # Recommendations
    print(f"\n💡 RECOMMENDATIONS:")
    
    if len(perfect_rssi) > wireless_count * 0.1:
        print(f"   ⚠️  Over 10% of clients have suspiciously strong signal")
        print(f"      This suggests mesh APs or bridges may be counted as clients")
        print(f"      Check: Are your mesh APs showing up in the client list?")
    
    if len(all_rssi) > 0:
        excellent_pct = (ranges['Excellent (> -50)'] / len(all_rssi)) * 100
        if excellent_pct > 70:
            print(f"   ⚠️  {excellent_pct:.0f}% of clients show 'Excellent' signal")
            print(f"      Possible causes:")
            print(f"      1. Many wired cameras being counted as wireless")
            print(f"      2. IoT devices placed very close to APs")
            print(f"      3. API returning incorrect values")
            print(f"      4. Most devices actually ARE close to APs (good!)")
    
    print()

if __name__ == "__main__":
    diagnose_rssi_readings()
