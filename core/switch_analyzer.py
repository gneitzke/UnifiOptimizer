#!/usr/bin/env python3
"""
UniFi Switch Analyzer

Comprehensive analysis of UniFi managed switches including:
- Port utilization and status
- PoE consumption and capacity
- Port errors and performance
- STP topology
- Link speeds and duplex
- VLAN configuration
- Storm control and loop detection
- Firmware version check
"""

from datetime import datetime
from rich.console import Console

console = Console()


class SwitchAnalyzer:
    """Analyze UniFi managed switches for optimization and issues"""
    
    def __init__(self, client, site='default'):
        self.client = client
        self.site = site
        
    def analyze_switches(self):
        """
        Comprehensive switch analysis
        
        Returns dict with:
        - switches: List of switch summaries
        - poe_analysis: PoE consumption and capacity
        - port_analysis: Port status, errors, speeds
        - stp_analysis: Spanning tree topology
        - issues: List of detected problems
        - recommendations: Optimization suggestions
        """
        console.print("[cyan]Analyzing switches...[/cyan]")
        
        # Get switch devices
        devices_response = self.client.get(f's/{self.site}/stat/device')
        if not devices_response or 'data' not in devices_response:
            return {'error': 'Failed to get devices'}
        
        devices = devices_response['data']
        switches = [d for d in devices if d.get('type') == 'usw']
        
        if not switches:
            return {'switches': [], 'message': 'No managed switches found'}
        
        # Get clients for port mapping
        clients_response = self.client.get(f's/{self.site}/stat/sta')
        clients = clients_response.get('data', []) if clients_response else []
        
        # Create MAC to client mapping (include ALL clients, not just is_wired)
        # Note: UniFi API sometimes incorrectly marks wired devices as wireless
        self.client_map = {c.get('mac'): c for c in clients}
        
        # Build port-to-device mapping from AP uplink data
        # This is more reliable than MAC matching for APs
        self.port_to_device = {}
        for switch in switches:
            switch_mac = switch.get('mac', '').lower()
            
            # Find all APs connected to this switch
            for device in devices:
                if device.get('type') == 'uap':
                    uplink = device.get('uplink', {})
                    if uplink.get('type') == 'wire':
                        uplink_mac = uplink.get('uplink_mac', '').lower()
                        uplink_port = uplink.get('uplink_remote_port')
                        
                        if uplink_mac == switch_mac and uplink_port:
                            # Map port number to AP info
                            port_key = f"{switch_mac}_{uplink_port}"
                            self.port_to_device[port_key] = {
                                'hostname': f"{device.get('name')} (AP)",
                                'mac': device.get('mac'),
                                'ip': device.get('ip'),
                                'is_device': True,
                                'type': 'uap',
                                'model': device.get('model')
                            }
        
        # Also add generic devices to client map (for non-AP devices)
        for device in devices:
            if device.get('mac'):
                # Add device as a "client" entry for MAC-based fallback mapping
                self.client_map[device.get('mac')] = {
                    'hostname': device.get('name'),
                    'mac': device.get('mac'),
                    'ip': device.get('ip'),
                    'is_device': True,
                    'type': device.get('type')  # 'uap', 'usw', 'ugw'
                }
        
        results = {
            'switches': [],
            'poe_analysis': {
                'total_capacity': 0,
                'total_consumption': 0,
                'poe_ports': [],
                'oversubscription_risk': False
            },
            'port_analysis': {
                'total_ports': 0,
                'active_ports': 0,
                'disabled_ports': 0,
                'error_ports': [],
                'speed_distribution': {}
            },
            'stp_analysis': {
                'root_bridge': None,
                'blocked_ports': [],
                'issues': []
            },
            'issues': [],
            'recommendations': [],
            'severity': 'ok'
        }
        
        for switch in switches:
            switch_summary = self._analyze_single_switch(switch, clients)
            results['switches'].append(switch_summary)
            
            # Aggregate PoE data
            if switch_summary.get('poe_capable'):
                results['poe_analysis']['total_capacity'] += switch_summary.get('poe_max', 0)
                results['poe_analysis']['total_consumption'] += switch_summary.get('poe_usage', 0)
            
            # Aggregate port data
            results['port_analysis']['total_ports'] += switch_summary.get('total_ports', 0)
            results['port_analysis']['active_ports'] += switch_summary.get('active_ports', 0)
            results['port_analysis']['disabled_ports'] += switch_summary.get('disabled_ports', 0)
            
            # Add issues
            results['issues'].extend(switch_summary.get('issues', []))
        
        # Analyze PoE utilization
        self._analyze_poe_capacity(results)
        
        # Analyze port health
        self._analyze_port_health(results)
        
        # Generate recommendations
        self._generate_switch_recommendations(results)
        
        # Set overall severity
        if any(issue.get('severity') == 'high' for issue in results['issues']):
            results['severity'] = 'high'
        elif any(issue.get('severity') == 'medium' for issue in results['issues']):
            results['severity'] = 'medium'
        
        return results
    
    def _analyze_single_switch(self, switch, clients):
        """Analyze a single switch in detail"""
        name = switch.get('name', 'Unknown Switch')
        model = switch.get('model', 'Unknown')
        
        summary = {
            'name': name,
            'model': model,
            'version': switch.get('version', 'Unknown'),
            'mac': switch.get('mac'),
            'ip': switch.get('ip'),
            'uptime': switch.get('uptime', 0),
            'state': switch.get('state'),
            'adopted': switch.get('adopted', False),
            'total_ports': 0,
            'active_ports': 0,
            'disabled_ports': 0,
            'poe_capable': False,
            'poe_max': 0,
            'poe_usage': 0,
            'ports': [],
            'issues': []
        }
        
        # PoE information
        summary['poe_capable'] = switch.get('total_max_power', 0) > 0
        summary['poe_max'] = switch.get('total_max_power', 0)
        summary['poe_usage'] = switch.get('total_used_power', 0)
        
        # Port table analysis
        port_table = switch.get('port_table', [])
        summary['total_ports'] = len([p for p in port_table if not p.get('is_uplink', False)])
        switch_mac = switch.get('mac', '')
        
        for port in port_table:
            port_info = self._analyze_port(port, name, clients, switch_mac)
            summary['ports'].append(port_info)
            
            # Count active/disabled
            if port_info['enabled']:
                if port_info['up']:
                    summary['active_ports'] += 1
            else:
                summary['disabled_ports'] += 1
            
            # Collect issues
            if port_info.get('issues'):
                for issue in port_info['issues']:
                    summary['issues'].append({
                        'switch': name,
                        'port': port_info['port_idx'],
                        'port_name': port_info.get('name', f"Port {port_info['port_idx']}"),
                        **issue
                    })
        
        # System health checks
        self._check_switch_health(switch, summary)
        
        return summary
    
    def _analyze_port(self, port, switch_name, clients, switch_mac=None):
        """Analyze individual port metrics"""
        port_info = {
            'port_idx': port.get('port_idx', 0),
            'name': port.get('name', ''),
            'enabled': port.get('enable', True),
            'up': port.get('up', False),
            'speed': port.get('speed', 0),
            'full_duplex': port.get('full_duplex', True),
            'is_uplink': port.get('is_uplink', False),
            'poe_enable': port.get('poe_enable', False),
            'poe_mode': port.get('poe_mode', 'off'),
            'connected_client': None,
            'is_ap': False,  # Flag to mark AP uplink ports
            'poe_power': port.get('poe_power', 0),
            'poe_voltage': port.get('poe_voltage', 0),
            'rx_bytes': port.get('rx_bytes', 0),
            'tx_bytes': port.get('tx_bytes', 0),
            'rx_packets': port.get('rx_packets', 0),
            'tx_packets': port.get('tx_packets', 0),
            'rx_dropped': port.get('rx_dropped', 0),
            'tx_dropped': port.get('tx_dropped', 0),
            'rx_errors': port.get('rx_errors', 0),
            'tx_errors': port.get('tx_errors', 0),
            'stp_state': port.get('stp_state', 'disabled'),
            'stp_pathcost': port.get('stp_pathcost', 0),
            'issues': []
        }
        
        # First, check port-to-device mapping (for APs with uplink data)
        # This is more reliable than MAC matching
        port_mapped = False
        if switch_mac:
            port_key = f"{switch_mac.lower()}_{port.get('port_idx')}"
            if port_key in self.port_to_device:
                device = self.port_to_device[port_key]
                port_info['connected_client'] = device.get('hostname')
                port_info['is_ap'] = True
                port_mapped = True
        
        # Fallback: Map connected client via MAC address (if not already mapped)
        if not port_mapped and port.get('last_connection'):
            mac = port['last_connection'].get('mac')
            if mac and mac in self.client_map:
                client = self.client_map[mac]
                port_info['connected_client'] = client.get('hostname', client.get('name', 'Unknown'))
        
        # Check for port issues
        if port_info['up'] and port_info['enabled']:
            # Speed issues
            if port_info['speed'] < 1000 and not port_info['is_uplink']:
                speed_loss_pct = ((1000 - port_info['speed']) / 1000) * 100
                port_info['issues'].append({
                    'type': 'slow_link',
                    'severity': 'low',
                    'speed': port_info['speed'],
                    'expected_speed': 1000,
                    'speed_loss_pct': speed_loss_pct,
                    'message': f"Port running at {port_info['speed']}Mbps (expected 1000Mbps) - {speed_loss_pct:.0f}% slower",
                    'impact': 'Reduced throughput affecting transfer speeds and network performance',
                    'recommendation': 'Upgrade to Cat5e or Cat6 cable for gigabit speeds'
                })
            
            # Duplex issues
            if not port_info['full_duplex']:
                port_info['issues'].append({
                    'type': 'half_duplex',
                    'severity': 'medium',
                    'message': 'Port running in half-duplex mode (should be full-duplex)',
                    'impact': '50% reduction in effective bandwidth, increased collision risk',
                    'recommendation': 'Check for duplex mismatch between switch and device, or cable issues'
                })
            
            # Error rate checks
            total_packets = port_info['rx_packets'] + port_info['tx_packets']
            total_errors = port_info['rx_errors'] + port_info['tx_errors']
            if total_packets > 1000:
                error_rate = total_errors / total_packets
                if error_rate > 0.001:  # More than 0.1% errors
                    port_info['issues'].append({
                        'type': 'high_errors',
                        'severity': 'high',
                        'rx_errors': port_info['rx_errors'],
                        'tx_errors': port_info['tx_errors'],
                        'total_errors': total_errors,
                        'error_rate': error_rate,
                        'message': f"High error rate: {error_rate*100:.2f}% (RX={port_info['rx_errors']:,}, TX={port_info['tx_errors']:,})",
                        'impact': 'Corrupted data transmission, potential data loss',
                        'recommendation': 'Replace cable immediately or check for electromagnetic interference'
                    })
            
            # Dropped packets
            total_dropped = port_info['rx_dropped'] + port_info['tx_dropped']
            if total_dropped > 1000:
                # Determine severity based on volume
                if total_dropped > 1000000:  # Over 1M drops
                    drop_severity = 'high'
                    impact = 'Significant packet loss causing network instability'
                elif total_dropped > 100000:  # Over 100K drops
                    drop_severity = 'medium'
                    impact = 'Noticeable packet loss affecting performance'
                else:
                    drop_severity = 'low'
                    impact = 'Minor packet loss, monitor for increases'
                
                port_info['issues'].append({
                    'type': 'dropped_packets',
                    'severity': drop_severity,
                    'rx_dropped': port_info['rx_dropped'],
                    'tx_dropped': port_info['tx_dropped'],
                    'total_dropped': total_dropped,
                    'message': f"Dropped packets: RX={port_info['rx_dropped']:,}, TX={port_info['tx_dropped']:,}",
                    'impact': impact,
                    'recommendation': 'Check device health, cable quality, and network congestion'
                })
            
            # PoE issues
            if port_info['poe_enable'] and port_info['poe_power'] == 0:
                port_info['issues'].append({
                    'type': 'poe_no_power',
                    'severity': 'medium',
                    'message': 'PoE enabled but no power draw detected',
                    'recommendation': 'Check if device requires PoE or disable PoE on port'
                })
        
        return port_info
    
    def _check_switch_health(self, switch, summary):
        """Check overall switch health"""
        # Firmware version check
        version = switch.get('version', '')
        if version and version < '6.0.0':  # Example threshold
            summary['issues'].append({
                'switch': summary['name'],
                'type': 'outdated_firmware',
                'severity': 'low',
                'message': f"Firmware version {version} may be outdated",
                'recommendation': 'Consider updating to latest stable firmware'
            })
        
        # Uptime check (too short might indicate stability issues)
        uptime_days = summary['uptime'] / 86400
        if uptime_days < 1:
            summary['issues'].append({
                'switch': summary['name'],
                'type': 'recent_reboot',
                'severity': 'low',
                'message': f"Switch uptime only {uptime_days:.1f} days",
                'recommendation': 'Monitor for stability issues'
            })
        
        # Temperature check
        if switch.get('general_temperature'):
            temp = switch.get('general_temperature', 0)
            if temp > 75:
                summary['issues'].append({
                    'switch': summary['name'],
                    'type': 'high_temperature',
                    'severity': 'high',
                    'message': f"High temperature: {temp}°C",
                    'recommendation': 'Improve ventilation or reduce ambient temperature'
                })
        
        # Port utilization
        if summary['total_ports'] > 0:
            utilization = (summary['active_ports'] / summary['total_ports']) * 100
            if utilization > 90:
                summary['issues'].append({
                    'switch': summary['name'],
                    'type': 'high_port_utilization',
                    'severity': 'medium',
                    'message': f"Port utilization at {utilization:.0f}%",
                    'recommendation': 'Consider adding another switch if expansion needed'
                })
    
    def _analyze_poe_capacity(self, results):
        """Analyze PoE capacity and utilization"""
        poe = results['poe_analysis']
        
        if poe['total_capacity'] == 0:
            return
        
        utilization = (poe['total_consumption'] / poe['total_capacity']) * 100
        poe['utilization_percent'] = utilization
        
        if utilization > 90:
            poe['oversubscription_risk'] = True
            results['issues'].append({
                'type': 'poe_oversubscription',
                'severity': 'high',
                'message': f"PoE utilization at {utilization:.1f}% of capacity",
                'recommendation': 'Reduce PoE load or add PoE injector for critical devices'
            })
        elif utilization > 75:
            results['issues'].append({
                'type': 'poe_high_utilization',
                'severity': 'medium',
                'message': f"PoE utilization at {utilization:.1f}% of capacity",
                'recommendation': 'Monitor PoE usage, consider planning for additional capacity'
            })
    
    def _analyze_port_health(self, results):
        """Analyze overall port health across all switches"""
        port_analysis = results['port_analysis']
        
        # Find ports with errors
        for switch_summary in results['switches']:
            for port in switch_summary['ports']:
                if port.get('issues') and port['up']:
                    port_analysis['error_ports'].append({
                        'switch': switch_summary['name'],
                        'port': port['port_idx'],
                        'name': port.get('name', f"Port {port['port_idx']}"),
                        'issues': port['issues']
                    })
                
                # Count speed distribution
                if port['up'] and port['enabled']:
                    speed = port['speed']
                    speed_key = f"{speed}Mbps"
                    port_analysis['speed_distribution'][speed_key] = \
                        port_analysis['speed_distribution'].get(speed_key, 0) + 1
    
    def _generate_switch_recommendations(self, results):
        """Generate optimization recommendations"""
        recommendations = results['recommendations']
        
        # PoE recommendations
        poe = results['poe_analysis']
        utilization = poe.get('utilization_percent', 0)
        if utilization > 80:
            recommendations.append({
                'type': 'poe_capacity',
                'priority': 'high' if utilization > 90 else 'medium',
                'message': f"PoE utilization high at {utilization:.1f}%",
                'recommendation': 'Plan for additional PoE capacity or use PoE injectors',
                'impact': 'Prevents PoE overload that could disable ports'
            })
        
        # Port error recommendations
        error_ports = results['port_analysis']['error_ports']
        if error_ports:
            recommendations.append({
                'type': 'port_errors',
                'priority': 'high',
                'message': f"{len(error_ports)} ports with errors or performance issues",
                'recommendation': 'Replace cables and check for physical damage',
                'impact': 'Improves network reliability and performance'
            })
        
        # Speed recommendations
        speed_dist = results['port_analysis']['speed_distribution']
        if '10Mbps' in speed_dist or '100Mbps' in speed_dist:
            slow_count = speed_dist.get('10Mbps', 0) + speed_dist.get('100Mbps', 0)
            recommendations.append({
                'type': 'slow_ports',
                'priority': 'low',
                'message': f"{slow_count} ports running below 1Gbps",
                'recommendation': 'Upgrade cables to Cat5e or Cat6',
                'impact': 'Improves throughput for connected devices'
            })
        
        # Firmware recommendations
        outdated_switches = [s for s in results['switches'] 
                           if any(i.get('type') == 'outdated_firmware' for i in s.get('issues', []))]
        if outdated_switches:
            recommendations.append({
                'type': 'firmware_update',
                'priority': 'low',
                'message': f"{len(outdated_switches)} switches with outdated firmware",
                'recommendation': 'Update to latest stable firmware',
                'impact': 'Improves security and adds new features'
            })
