#!/usr/bin/env python3
"""
Expert Network Analyzer - Comprehensive UniFi Network Analysis
Includes RSSI analysis, historical lookback, mesh AP optimization, and best practices
"""

from datetime import datetime, timedelta
from collections import defaultdict
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


class ExpertNetworkAnalyzer:
    """Expert-level network analysis with historical data and best practices"""
    
    # Best practice thresholds
    RSSI_EXCELLENT = -50
    RSSI_GOOD = -60
    RSSI_FAIR = -70
    RSSI_POOR = -80
    
    # Mesh AP specific thresholds (more tolerant for reliability)
    MESH_RSSI_ACCEPTABLE = -75  # Mesh uplinks can tolerate weaker signal
    MESH_DISCONNECT_THRESHOLD = 5  # Mesh APs should be very stable
    
    # Channel recommendations
    CHANNEL_24_PREFERRED = [1, 6, 11]  # Non-overlapping channels
    CHANNEL_5_DFS_START = 52  # DFS channels start here
    
    def __init__(self, client, site='default'):
        self.client = client
        self.site = site
        self.devices = []
        self.clients = []
        self.events = []
        self.historical_events = []
        
    def collect_data(self, lookback_days=3):
        """
        Collect network data including historical lookback
        
        Args:
            lookback_days: Number of days to look back for events
        """
        console.print(f"[cyan]Collecting network data (last {lookback_days} days)...[/cyan]")
        
        # Get devices
        devices_response = self.client.get(f's/{self.site}/stat/device')
        self.devices = devices_response.get('data', []) if devices_response else []
        
        # Get current clients
        clients_response = self.client.get(f's/{self.site}/stat/sta')
        self.clients = clients_response.get('data', []) if clients_response else []
        
        # Get recent events (default is usually last 1 hour)
        events_response = self.client.get(f's/{self.site}/stat/event')
        self.events = events_response.get('data', []) if events_response else []
        
        # Get historical events (last N days)
        # Calculate timestamp for N days ago (in milliseconds)
        lookback_ms = int((datetime.now() - timedelta(days=lookback_days)).timestamp() * 1000)
        
        try:
            historical_response = self.client.get(f's/{self.site}/stat/event', 
                                                 params={'within': lookback_days * 24})
            self.historical_events = historical_response.get('data', []) if historical_response else []
        except:
            # Fallback to regular events if historical query fails
            self.historical_events = self.events
        
        console.print(f"[green]✓ {len(self.devices)} devices, {len(self.clients)} clients, "
                     f"{len(self.historical_events)} events collected[/green]\n")
    
    def analyze_aps(self):
        """
        Analyze access points with expert-level recommendations
        
        Returns:
            dict: AP analysis with recommendations
        """
        aps = [d for d in self.devices if d.get('type') == 'uap']
        
        analysis = {
            'total_aps': len(aps),
            'wired_aps': [],
            'mesh_aps': [],
            'ap_details': [],
            'channel_usage': defaultdict(list),
            'power_settings': defaultdict(list),
            'issues': []
        }
        
        for ap in aps:
            ap_mac = ap.get('mac')
            ap_name = ap.get('name', 'Unnamed AP')
            uplink = ap.get('uplink', {})
            uplink_type = uplink.get('type', 'wire')
            is_mesh = uplink_type == 'wireless'
            
            # Get uplink details for mesh APs
            uplink_remote_mac = uplink.get('uplink_remote_mac') if is_mesh else None
            uplink_rssi = uplink.get('rssi') if is_mesh else None
            
            # Analyze radios
            radio_table = ap.get('radio_table', [])
            radios = {}
            
            for radio in radio_table:
                radio_name = radio.get('radio')
                if not radio_name:
                    continue
                    
                band = '2.4GHz' if radio_name == 'ng' else '5GHz' if radio_name == 'na' else '6GHz'
                # Ensure channel is an integer for comparisons
                channel = radio.get('channel', 0)
                try:
                    channel = int(channel)
                except (ValueError, TypeError):
                    channel = 0
                    
                ht = radio.get('ht', 20)
                tx_power = radio.get('tx_power')
                tx_power_mode = radio.get('tx_power_mode', 'auto')
                
                radios[band] = {
                    'radio': radio_name,
                    'channel': channel,
                    'width': ht,
                    'tx_power': tx_power,
                    'tx_power_mode': tx_power_mode
                }
                
                # Track channel usage
                analysis['channel_usage'][f"{band}_ch{channel}"].append(ap_name)
                
                # Track power settings
                analysis['power_settings'][f"{band}_{tx_power_mode}"].append(ap_name)
            
            # Get clients on this AP
            ap_clients = [c for c in self.clients if c.get('ap_mac') == ap_mac]
            
            ap_info = {
                'name': ap_name,
                'mac': ap_mac,
                'model': ap.get('model', 'Unknown'),
                'is_mesh': is_mesh,
                'uplink_type': uplink_type,
                'uplink_rssi': uplink_rssi,
                'uplink_remote_mac': uplink_remote_mac,
                'radios': radios,
                'client_count': len(ap_clients),
                'clients': ap_clients,
                'state': ap.get('state', 0),
                'uptime': ap.get('uptime', 0)
            }
            
            analysis['ap_details'].append(ap_info)
            
            if is_mesh:
                analysis['mesh_aps'].append(ap_info)
            else:
                analysis['wired_aps'].append(ap_info)
        
        return analysis
    
    def analyze_mesh_aps(self, ap_analysis):
        """
        Special analysis for mesh APs (reliability focused)
        
        Args:
            ap_analysis: Results from analyze_aps()
        
        Returns:
            list: Mesh-specific recommendations
        """
        recommendations = []
        
        console.print(f"[bold cyan]Analyzing {len(ap_analysis['mesh_aps'])} Mesh APs[/bold cyan]")
        console.print("[dim]Mesh APs prioritize reliability over speed[/dim]\n")
        
        for mesh_ap in ap_analysis['mesh_aps']:
            ap_name = mesh_ap['name']
            uplink_rssi = mesh_ap['uplink_rssi']
            
            # Critical: Check mesh uplink signal strength
            if uplink_rssi and uplink_rssi < -80:
                recommendations.append({
                    'ap': mesh_ap,
                    'priority': 'critical',
                    'issue': 'weak_mesh_uplink',
                    'message': f"Mesh uplink RSSI {uplink_rssi} dBm is too weak (< -80 dBm)",
                    'recommendation': 'Reposition AP or add intermediate mesh hop for reliability',
                    'type': 'mesh_reliability'
                })
            elif uplink_rssi and uplink_rssi < self.MESH_RSSI_ACCEPTABLE:
                recommendations.append({
                    'ap': mesh_ap,
                    'priority': 'high',
                    'issue': 'marginal_mesh_uplink',
                    'message': f"Mesh uplink RSSI {uplink_rssi} dBm is marginal",
                    'recommendation': 'Monitor for disconnections; consider repositioning',
                    'type': 'mesh_reliability'
                })
            
            # Check for high power on mesh APs (can cause issues)
            for band, radio in mesh_ap['radios'].items():
                if radio['tx_power_mode'] == 'high':
                    recommendations.append({
                        'ap': mesh_ap,
                        'radio': radio,
                        'band': band,
                        'priority': 'medium',
                        'issue': 'mesh_high_power',
                        'message': f"{band} power set to HIGH on mesh AP",
                        'recommendation': 'Use MEDIUM or AUTO for mesh APs to improve stability',
                        'type': 'mesh_power'
                    })
            
            # Check for DFS channels on mesh APs (can cause disconnects)
            for band, radio in mesh_ap['radios'].items():
                channel = radio['channel']
                if band == '5GHz' and self.CHANNEL_5_DFS_START <= channel <= 144:
                    recommendations.append({
                        'ap': mesh_ap,
                        'radio': radio,
                        'band': band,
                        'priority': 'medium',
                        'issue': 'mesh_dfs_channel',
                        'message': f"{band} using DFS channel {channel}",
                        'recommendation': 'DFS channels can cause brief disconnects on radar detection. '
                                        'Consider non-DFS channels (36-48, 149-165) for mesh reliability',
                        'type': 'mesh_channel'
                    })
            
            # Check mesh AP disconnect history
            mesh_disconnects = [e for e in self.historical_events 
                               if e.get('ap') == mesh_ap['mac'] and 
                               ('disconnect' in e.get('key', '').lower() or 
                                'offline' in e.get('key', '').lower())]
            
            if len(mesh_disconnects) > self.MESH_DISCONNECT_THRESHOLD:
                recommendations.append({
                    'ap': mesh_ap,
                    'priority': 'high',
                    'issue': 'mesh_unstable',
                    'message': f"{len(mesh_disconnects)} disconnect events in lookback period",
                    'recommendation': 'Investigate mesh uplink signal, interference, or power issues',
                    'type': 'mesh_reliability'
                })
        
        return recommendations
    
    def analyze_channels(self, ap_analysis):
        """
        Analyze channel usage and recommend optimization (smart tracking to avoid repeated recommendations)
        
        Args:
            ap_analysis: Results from analyze_aps()
        
        Returns:
            list: Channel-related recommendations (filtered to avoid repeats)
        """
        # Use smart channel analyzer that tracks recommendations
        from core.channel_optimizer import analyze_channels_smart, ChannelRecommendationTracker
        
        tracker = ChannelRecommendationTracker()
        recommendations = analyze_channels_smart(ap_analysis, tracker)
        
        # Check 5GHz channel width (40MHz is often better than 80MHz in dense environments)
        for ap_info in ap_analysis['ap_details']:
            if '5GHz' in ap_info['radios']:
                radio = ap_info['radios']['5GHz']
                width = radio['width']
                client_count = ap_info['client_count']
                
                if width == 80 and len(ap_analysis['ap_details']) > 2 and client_count < 5:
                    recommendations.append({
                        'ap': ap_info,
                        'radio': radio,
                        'band': '5GHz',
                        'priority': 'low',
                        'issue': 'wide_channel',
                        'message': f"Using 80MHz width with only {client_count} clients",
                        'recommendation': "Consider 40MHz for less interference in multi-AP environments",
                        'type': 'channel_width'
                    })
        
        return recommendations
    
    def analyze_power_settings(self, ap_analysis):
        """
        Analyze transmit power settings
        
        Args:
            ap_analysis: Results from analyze_aps()
        
        Returns:
            list: Power-related recommendations
        """
        recommendations = []
        
        # Count high power APs
        high_power_aps = []
        for ap_info in ap_analysis['ap_details']:
            for band, radio in ap_info['radios'].items():
                if radio['tx_power_mode'] == 'high':
                    high_power_aps.append((ap_info, band))
        
        # If multiple APs on high power, recommend reduction
        if len(high_power_aps) > 1:
            for ap_info, band in high_power_aps:
                recommendations.append({
                    'ap': ap_info,
                    'radio': ap_info['radios'][band],
                    'band': band,
                    'priority': 'medium',
                    'issue': 'high_power',
                    'message': f"{band} power set to HIGH",
                    'recommendation': "HIGH power can cause roaming issues and co-channel interference. "
                                    "Use MEDIUM or AUTO for better coverage overlap and roaming.",
                    'type': 'power_optimization'
                })
        
        return recommendations
    
    def analyze_client_health_historical(self):
        """
        Analyze client health with historical data
        
        Returns:
            dict: Client health analysis with historical trends
        """
        # Track disconnections per client over lookback period
        client_disconnects = defaultdict(list)
        client_roams = defaultdict(list)
        
        for event in self.historical_events:
            event_key = event.get('key', '')
            client_mac = event.get('user') or event.get('client')
            timestamp = event.get('time', 0)
            
            if 'disconnect' in event_key.lower():
                client_disconnects[client_mac].append(timestamp)
            elif 'roam' in event_key.lower():
                client_roams[client_mac].append(timestamp)
        
        # Analyze current clients with historical context
        client_analysis = []
        
        for client in self.clients:
            mac = client.get('mac')
            hostname = client.get('hostname', 'Unknown')
            rssi = client.get('rssi', -100)
            
            # FIX: Some UniFi controllers return positive RSSI values
            # RSSI should always be negative in dBm for WiFi
            if rssi > 0:
                rssi = -rssi
            
            ap_mac = client.get('ap_mac')
            channel = client.get('channel', 0)
            
            # Find AP name
            ap_name = 'Unknown'
            for ap in self.devices:
                if ap.get('mac') == ap_mac:
                    ap_name = ap.get('name', 'Unknown')
                    break
            
            # Get historical stats
            disconnect_count = len(client_disconnects.get(mac, []))
            roam_count = len(client_roams.get(mac, []))
            
            # Calculate health score
            if rssi > self.RSSI_EXCELLENT:
                signal_score = 100
            elif rssi > self.RSSI_GOOD:
                signal_score = 80
            elif rssi > self.RSSI_FAIR:
                signal_score = 60
            elif rssi > self.RSSI_POOR:
                signal_score = 40
            else:
                signal_score = 20
            
            # Penalties
            disconnect_penalty = min(disconnect_count * 5, 50)  # Max 50 point penalty
            roam_penalty = min(roam_count * 2, 20)  # Max 20 point penalty
            
            health_score = max(0, signal_score - disconnect_penalty - roam_penalty)
            
            client_analysis.append({
                'mac': mac,
                'hostname': hostname,
                'ip': client.get('ip', 'Unknown'),
                'rssi': rssi,
                'ap_name': ap_name,
                'ap_mac': ap_mac,
                'channel': channel,
                'signal_score': signal_score,
                'disconnect_count': disconnect_count,
                'roam_count': roam_count,
                'health_score': health_score,
                'grade': self._get_grade(health_score)
            })
        
        # Sort by health score (worst first)
        client_analysis.sort(key=lambda x: x['health_score'])
        
        # Calculate signal distribution for histogram
        signal_distribution = {
            'excellent': 0,
            'good': 0,
            'fair': 0,
            'poor': 0,
            'critical': 0,
            'wired': 0
        }
        
        for client in self.clients:
            if client.get('is_wired', False):
                signal_distribution['wired'] += 1
            else:
                rssi = client.get('rssi', -100)
                
                # FIX: Some UniFi controllers return positive RSSI values
                # RSSI should always be negative in dBm for WiFi
                if rssi > 0:
                    rssi = -rssi
                
                if rssi > self.RSSI_EXCELLENT:
                    signal_distribution['excellent'] += 1
                elif rssi > self.RSSI_GOOD:
                    signal_distribution['good'] += 1
                elif rssi > self.RSSI_FAIR:
                    signal_distribution['fair'] += 1
                elif rssi > self.RSSI_POOR:
                    signal_distribution['poor'] += 1
                else:
                    signal_distribution['critical'] += 1
        
        return {
            'clients': client_analysis,
            'total_clients': len(client_analysis),
            'weak_signal': [c for c in client_analysis if c['rssi'] < self.RSSI_FAIR],
            'frequent_disconnects': [c for c in client_analysis if c['disconnect_count'] >= 3],
            'poor_health': [c for c in client_analysis if c['health_score'] < 60],
            'signal_distribution': signal_distribution
        }
    
    def generate_expert_recommendations(self, ap_analysis, client_analysis):
        """
        Generate comprehensive expert recommendations
        
        Args:
            ap_analysis: AP analysis results
            client_analysis: Client health analysis results
        
        Returns:
            list: Prioritized recommendations
        """
        recommendations = []
        
        # Analyze mesh APs first (reliability critical)
        if ap_analysis['mesh_aps']:
            mesh_recs = self.analyze_mesh_aps(ap_analysis)
            recommendations.extend(mesh_recs)
        
        # Analyze channels
        channel_recs = self.analyze_channels(ap_analysis)
        recommendations.extend(channel_recs)
        
        # Analyze power settings
        power_recs = self.analyze_power_settings(ap_analysis)
        recommendations.extend(power_recs)
        
        # Client-based recommendations
        weak_clients_by_ap = defaultdict(list)
        for client in client_analysis['weak_signal']:
            weak_clients_by_ap[client['ap_mac']].append(client)
        
        for ap_mac, clients in weak_clients_by_ap.items():
            if len(clients) >= 2:  # Multiple weak clients on same AP
                # Find AP info
                ap_info = next((ap for ap in ap_analysis['ap_details'] if ap['mac'] == ap_mac), None)
                if ap_info:
                    avg_rssi = sum(c['rssi'] for c in clients) / len(clients)
                    recommendations.append({
                        'ap': ap_info,
                        'priority': 'high',
                        'issue': 'weak_coverage',
                        'message': f"{len(clients)} clients with weak signal (avg {avg_rssi:.1f} dBm)",
                        'recommendation': "Check AP placement, power settings, or add additional AP for coverage",
                        'type': 'coverage',
                        'affected_clients': len(clients)
                    })
        
        # Disconnection-based recommendations
        disconnect_clients_by_ap = defaultdict(list)
        for client in client_analysis['frequent_disconnects']:
            disconnect_clients_by_ap[client['ap_mac']].append(client)
        
        for ap_mac, clients in disconnect_clients_by_ap.items():
            if len(clients) >= 2:  # Multiple problematic clients on same AP
                ap_info = next((ap for ap in ap_analysis['ap_details'] if ap['mac'] == ap_mac), None)
                if ap_info:
                    total_disconnects = sum(c['disconnect_count'] for c in clients)
                    recommendations.append({
                        'ap': ap_info,
                        'priority': 'high',
                        'issue': 'frequent_disconnects',
                        'message': f"{len(clients)} clients with {total_disconnects} total disconnects",
                        'recommendation': "Investigate interference, try different channel, check for DFS radar events",
                        'type': 'stability',
                        'affected_clients': len(clients)
                    })
        
        # Sort by priority
        priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        recommendations.sort(key=lambda x: (
            priority_order.get(x['priority'], 3),
            -x.get('affected_clients', 0)
        ))
        
        return recommendations
    
    def _get_grade(self, score):
        """Convert score to letter grade"""
        if score >= 90:
            return 'A'
        elif score >= 80:
            return 'B'
        elif score >= 70:
            return 'C'
        elif score >= 60:
            return 'D'
        else:
            return 'F'


def run_expert_analysis(client, site='default', lookback_days=3):
    """
    Run complete expert network analysis
    
    Args:
        client: CloudKey Gen2+ API client
        site: Site name
        lookback_days: Days of historical data to analyze
    
    Returns:
        dict: Complete analysis results
    """
    analyzer = ExpertNetworkAnalyzer(client, site)
    
    # Collect data
    analyzer.collect_data(lookback_days)
    
    # Analyze APs
    ap_analysis = analyzer.analyze_aps()
    
    # Analyze clients with historical data
    client_analysis = analyzer.analyze_client_health_historical()
    
    # Generate expert recommendations
    recommendations = analyzer.generate_expert_recommendations(ap_analysis, client_analysis)
    
    return {
        'ap_analysis': ap_analysis,
        'client_analysis': client_analysis,
        'recommendations': recommendations,
        'lookback_days': lookback_days,
        'devices': analyzer.devices,  # Include full device list for change applier
        'clients': analyzer.clients  # Include full client list for advanced analysis
    }
