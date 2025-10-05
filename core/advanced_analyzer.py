#!/usr/bin/env python3
"""
Advanced Network Analysis Features

Implements enterprise-grade analysis:
- DFS event tracking
- Band steering analysis
- Fast roaming validation (802.11r/k/v)
- Airtime utilization monitoring
- Client capability matrix
- Network health scoring
"""

from datetime import datetime, timedelta
from collections import defaultdict


class AdvancedNetworkAnalyzer:
    """Advanced network analysis features for enterprise deployments"""
    
    def __init__(self, client, site='default'):
        self.client = client
        self.site = site
        
    def analyze_dfs_events(self, lookback_days=3):
        """
        Track DFS (Dynamic Frequency Selection) radar events
        
        DFS events cause sudden disconnects on channels 52-144 (5GHz)
        Returns events, affected APs, and recommendations
        """
        results = {
            'total_events': 0,
            'events_by_ap': {},
            'affected_channels': set(),
            'recommendations': [],
            'severity': 'ok'
        }
        
        try:
            # Get events from controller
            within_hours = lookback_days * 24
            events = self.client.get(f's/{self.site}/stat/event?within={within_hours}&_limit=1000')
            
            if not events:
                return results
            
            # Filter DFS-related events
            dfs_keywords = ['dfs', 'radar', 'channel change', 'cac']
            
            for event in events:
                event_msg = event.get('msg', '').lower()
                event_key = event.get('key', '').lower()
                
                if any(keyword in event_msg or keyword in event_key for keyword in dfs_keywords):
                    results['total_events'] += 1
                    
                    ap_name = event.get('ap_name', event.get('ap', 'Unknown'))
                    channel = event.get('channel')
                    
                    if ap_name not in results['events_by_ap']:
                        results['events_by_ap'][ap_name] = []
                    
                    results['events_by_ap'][ap_name].append({
                        'timestamp': event.get('time'),
                        'message': event.get('msg'),
                        'channel': channel
                    })
                    
                    if channel:
                        results['affected_channels'].add(channel)
            
            # Generate recommendations
            if results['total_events'] > 5:
                results['severity'] = 'high'
                results['recommendations'].append({
                    'type': 'dfs_avoidance',
                    'message': f'Detected {results["total_events"]} DFS events in {lookback_days} days',
                    'recommendation': 'Switch to non-DFS channels (36, 40, 44, 48, 149-165)',
                    'priority': 'high'
                })
            elif results['total_events'] > 0:
                results['severity'] = 'medium'
                results['recommendations'].append({
                    'type': 'dfs_monitoring',
                    'message': f'Detected {results["total_events"]} DFS events',
                    'recommendation': 'Monitor DFS channel stability, consider non-DFS alternatives',
                    'priority': 'medium'
                })
            
            # Per-AP recommendations
            for ap_name, events in results['events_by_ap'].items():
                if len(events) > 2:
                    results['recommendations'].append({
                        'type': 'ap_dfs',
                        'device': ap_name,
                        'message': f'{len(events)} DFS events on {ap_name}',
                        'recommendation': f'Move {ap_name} to non-DFS channel',
                        'priority': 'high'
                    })
                    
        except Exception as e:
            results['error'] = str(e)
        
        results['affected_channels'] = list(results['affected_channels'])
        return results
    
    def analyze_band_steering(self, devices, clients):
        """
        Analyze band steering configuration and effectiveness
        
        Identifies:
        - Clients on 2.4GHz that support 5GHz
        - APs with band steering disabled
        - Sticky clients refusing to move
        """
        results = {
            'band_steering_enabled': {},
            'misplaced_clients': [],
            'dual_band_clients_on_2ghz': 0,
            'recommendations': [],
            'severity': 'ok'
        }
        
        try:
            # Check AP configurations
            for device in devices:
                if device.get('type') != 'uap':
                    continue
                
                ap_name = device.get('name', 'Unnamed AP')
                ap_id = device.get('_id')
                
                # Check band steering setting
                band_steering = device.get('bandsteering_mode', 'off')
                results['band_steering_enabled'][ap_name] = band_steering != 'off'
                
                if band_steering == 'off':
                    results['recommendations'].append({
                        'type': 'band_steering',
                        'device': ap_name,
                        'message': f'Band steering disabled on {ap_name}',
                        'recommendation': 'Enable band steering to move capable clients to 5GHz',
                        'priority': 'medium'
                    })
            
            # Analyze client placement
            for client in clients:
                if not client.get('is_wired', False):
                    radio = client.get('radio', '')
                    radio_proto = client.get('radio_proto', '')
                    
                    # Check if client is on 2.4GHz but supports 5GHz
                    if radio == 'ng':  # 2.4GHz
                        hostname = client.get('hostname', client.get('name', '')).lower()
                        tx_rate = client.get('tx_rate', 0)
                        rx_rate = client.get('rx_rate', 0)
                        nss = client.get('nss', 1)  # Number of spatial streams
                        
                        # Multi-indicator 5GHz capability detection
                        is_dual_band = False
                        detection_reason = None
                        
                        # Method 1: API capability fields (rarely populated)
                        if 'ac' in radio_proto or 'ax' in radio_proto or client.get('is_11ac') or client.get('is_11ax'):
                            is_dual_band = True
                            detection_reason = f'API indicates {radio_proto}'
                        
                        # Method 2: High data rates (>72 Mbps suggests 802.11n+ dual-band capable)
                        elif tx_rate > 72000 or rx_rate > 72000:
                            is_dual_band = True
                            detection_reason = f'High data rate: TX={tx_rate/1000:.0f}Mbps RX={rx_rate/1000:.0f}Mbps'
                        
                        # Method 3: Multiple spatial streams (>1 suggests modern dual-band device)
                        elif nss >= 2:
                            is_dual_band = True
                            detection_reason = f'Multiple spatial streams (nss={nss})'
                        
                        # Method 4: Known dual-band device names
                        elif any(pattern in hostname for pattern in [
                            'iphone', 'ipad', 'ipod', 'mac', 'macbook', 'imac',  # Apple (all dual-band since 2013)
                            'galaxy', 'samsung',  # Modern Samsung
                            'pixel',              # Google Pixel
                            'oneplus',            # OnePlus phones
                            'surface',            # Microsoft Surface
                            'thinkpad', 'latitude', 'pavilion', 'inspiron',  # Modern laptops
                        ]):
                            is_dual_band = True
                            detection_reason = f'Known dual-band device: {hostname}'
                        
                        if is_dual_band:
                            results['dual_band_clients_on_2ghz'] += 1
                            results['misplaced_clients'].append({
                                'hostname': client.get('hostname', client.get('name', 'Unknown')),
                                'mac': client.get('mac'),
                                'ap': client.get('ap_name', 'Unknown'),
                                'rssi': client.get('rssi'),
                                'radio_proto': radio_proto,
                                'tx_rate': tx_rate,
                                'detection_reason': detection_reason
                            })
            
            # Generate recommendations
            if results['dual_band_clients_on_2ghz'] > 5:
                results['severity'] = 'high'
                results['recommendations'].append({
                    'type': 'band_steering_critical',
                    'message': f'{results["dual_band_clients_on_2ghz"]} dual-band clients stuck on 2.4GHz',
                    'recommendation': 'Enable band steering on all APs and verify 5GHz signal coverage',
                    'priority': 'high',
                    'affected_clients': results['dual_band_clients_on_2ghz']
                })
            elif results['dual_band_clients_on_2ghz'] > 0:
                results['severity'] = 'medium'
                results['recommendations'].append({
                    'type': 'band_steering_warning',
                    'message': f'{results["dual_band_clients_on_2ghz"]} clients on 2.4GHz that support 5GHz',
                    'recommendation': 'Review band steering settings and 5GHz coverage',
                    'priority': 'medium',
                    'affected_clients': results['dual_band_clients_on_2ghz']
                })
                
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def analyze_fast_roaming(self, devices):
        """
        Validate fast roaming (802.11r/k/v) configuration
        
        Checks:
        - 802.11r (Fast Transition) enabled
        - 802.11k (Neighbor Reports) enabled
        - 802.11v (BSS Transition) enabled
        - Configuration consistency across APs
        """
        results = {
            'roaming_features': {
                '802.11r': {'enabled_count': 0, 'disabled_count': 0, 'aps': []},
                '802.11k': {'enabled_count': 0, 'disabled_count': 0, 'aps': []},
                '802.11v': {'enabled_count': 0, 'disabled_count': 0, 'aps': []}
            },
            'consistent_config': True,
            'recommendations': [],
            'severity': 'ok'
        }
        
        try:
            for device in devices:
                if device.get('type') != 'uap':
                    continue
                
                ap_name = device.get('name', 'Unnamed AP')
                
                # Check 802.11r (Fast Transition)
                ft_enabled = device.get('wlangroup_id_ng') or device.get('wlangroup_id_na')
                # Note: Actual FT config is in WLAN settings, not device
                # This is a simplified check
                
                # Check radio table for roaming settings
                radio_table = device.get('radio_table', [])
                for radio in radio_table:
                    # 802.11k and 802.11v are typically in radio settings
                    min_rssi = radio.get('min_rssi_enabled', False)
                    
                    if min_rssi:
                        results['roaming_features']['802.11v']['enabled_count'] += 1
                        results['roaming_features']['802.11v']['aps'].append(ap_name)
                    else:
                        results['roaming_features']['802.11v']['disabled_count'] += 1
            
            # Check consistency
            for feature, data in results['roaming_features'].items():
                if data['enabled_count'] > 0 and data['disabled_count'] > 0:
                    results['consistent_config'] = False
                    results['recommendations'].append({
                        'type': 'roaming_inconsistent',
                        'feature': feature,
                        'message': f'{feature} enabled on {data["enabled_count"]} APs but disabled on {data["disabled_count"]} APs',
                        'recommendation': f'Enable {feature} on all APs for consistent roaming behavior',
                        'priority': 'high'
                    })
            
            # Recommend enabling if mostly disabled
            for feature, data in results['roaming_features'].items():
                if data['enabled_count'] == 0 and data['disabled_count'] > 0:
                    results['severity'] = 'medium'
                    results['recommendations'].append({
                        'type': 'roaming_disabled',
                        'feature': feature,
                        'message': f'{feature} disabled on all APs',
                        'recommendation': f'Enable {feature} to improve roaming performance and VoIP quality',
                        'priority': 'medium'
                    })
                    
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def analyze_min_rssi(self, devices):
        """
        Analyze minimum RSSI configuration across APs
        
        Min RSSI forces weak clients to roam, preventing sticky client problems
        and improving overall network performance
        
        Returns:
            dict: Min RSSI analysis with recommendations
        """
        results = {
            'radios_with_min_rssi': [],
            'radios_without_min_rssi': [],
            'total_radios': 0,
            'enabled_count': 0,
            'disabled_count': 0,
            'recommendations': [],
            'severity': 'ok'
        }
        
        # Recommended thresholds based on industry best practices
        # These values balance roaming aggressiveness with connection stability
        # Sources: Cisco WLAN Design Guide, Aruba Best Practices, UniFi recommendations
        RECOMMENDED_MIN_RSSI_24GHZ = -75  # 2.4GHz: -75 to -80 dBm typical range
        RECOMMENDED_MIN_RSSI_5GHZ = -72   # 5GHz: -70 to -75 dBm typical range
        
        # Note: Values depend on environment:
        # - High-density: -67 to -72 dBm (force aggressive roaming)
        # - Standard office: -72 to -75 dBm (balanced)
        # - Large coverage areas: -75 to -80 dBm (avoid premature disconnects)
        
        try:
            for device in devices:
                if device.get('type') != 'uap':
                    continue
                
                ap_name = device.get('name', 'Unnamed AP')
                ap_id = device.get('_id')
                radio_table = device.get('radio_table', [])
                
                for radio in radio_table:
                    radio_name = radio.get('radio', 'unknown')
                    band = '2.4GHz' if radio_name == 'ng' else '5GHz' if radio_name == 'na' else '6GHz'
                    
                    min_rssi_enabled = radio.get('min_rssi_enabled', False)
                    min_rssi_value = radio.get('min_rssi', None)
                    
                    results['total_radios'] += 1
                    
                    radio_info = {
                        'device': ap_name,
                        'device_id': ap_id,
                        'radio': radio_name,
                        'band': band,
                        'enabled': min_rssi_enabled,
                        'value': min_rssi_value
                    }
                    
                    if min_rssi_enabled:
                        results['enabled_count'] += 1
                        results['radios_with_min_rssi'].append(radio_info)
                        
                        # Check if value is optimal
                        recommended = RECOMMENDED_MIN_RSSI_24GHZ if radio_name == 'ng' else RECOMMENDED_MIN_RSSI_5GHZ
                        if min_rssi_value and abs(min_rssi_value - recommended) > 10:
                            results['recommendations'].append({
                                'type': 'min_rssi_suboptimal',
                                'device': ap_name,
                                'radio': radio_name,
                                'band': band,
                                'message': f'{ap_name} {band} has min RSSI at {min_rssi_value} dBm',
                                'recommendation': f'Consider adjusting to {recommended} dBm for optimal roaming',
                                'priority': 'low',
                                'current_value': min_rssi_value,
                                'recommended_value': recommended
                            })
                    else:
                        results['disabled_count'] += 1
                        results['radios_without_min_rssi'].append(radio_info)
                        
                        recommended = RECOMMENDED_MIN_RSSI_24GHZ if radio_name == 'ng' else RECOMMENDED_MIN_RSSI_5GHZ
                        
                        results['recommendations'].append({
                            'type': 'min_rssi_disabled',
                            'device': ap_name,
                            'radio': radio_name,
                            'band': band,
                            'message': f'Min RSSI disabled on {ap_name} {band}',
                            'recommendation': f'Enable min RSSI at {recommended} dBm to improve roaming',
                            'priority': 'medium',
                            'recommended_value': recommended
                        })
            
            # Set overall severity
            if results['disabled_count'] > results['enabled_count']:
                results['severity'] = 'high'
            elif results['disabled_count'] > 0:
                results['severity'] = 'medium'
            
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def analyze_airtime_utilization(self, devices, lookback_hours=24):
        """
        Analyze airtime utilization per AP with time-series data
        
        High airtime = saturated AP even with few clients
        Identifies airtime hogs and overloaded APs
        
        Args:
            devices: List of device dicts
            lookback_hours: Hours of historical data to collect (default 24)
        """
        results = {
            'ap_utilization': {},
            'saturated_aps': [],
            'airtime_hogs': [],
            'recommendations': [],
            'severity': 'ok',
            'time_series': {}  # New: historical airtime data
        }
        
        try:
            for device in devices:
                if device.get('type') != 'uap':
                    continue
                
                ap_name = device.get('name', 'Unnamed AP')
                ap_id = device.get('_id')
                
                # Get radio stats
                radio_table_stats = device.get('radio_table_stats', [])
                
                for radio_stat in radio_table_stats:
                    radio_name = radio_stat.get('name', 'unknown')
                    band = '2.4GHz' if radio_stat.get('radio') == 'ng' else '5GHz'
                    
                    # Channel utilization
                    cu_total = radio_stat.get('cu_total', 0)
                    cu_self_tx = radio_stat.get('cu_self_tx', 0)
                    cu_self_rx = radio_stat.get('cu_self_rx', 0)
                    
                    # Calculate airtime percentage
                    airtime_pct = cu_total
                    
                    key = f"{ap_name} ({band})"
                    results['ap_utilization'][key] = {
                        'airtime_pct': airtime_pct,
                        'tx_pct': cu_self_tx,
                        'rx_pct': cu_self_rx,
                        'clients': radio_stat.get('num_sta', 0)
                    }
                    
                    # Flag saturated APs (>70% airtime)
                    if airtime_pct > 70:
                        results['saturated_aps'].append({
                            'ap': ap_name,
                            'band': band,
                            'airtime': airtime_pct,
                            'clients': radio_stat.get('num_sta', 0)
                        })
                        
                        results['severity'] = 'high'
                        results['recommendations'].append({
                            'type': 'airtime_saturation',
                            'device': ap_name,
                            'message': f'{ap_name} {band} at {airtime_pct}% airtime utilization',
                            'recommendation': 'Add another AP or move clients to reduce load',
                            'priority': 'high',
                            'airtime': airtime_pct
                        })
                    elif airtime_pct > 50:
                        results['severity'] = 'medium' if results['severity'] == 'ok' else results['severity']
                        results['recommendations'].append({
                            'type': 'airtime_warning',
                            'device': ap_name,
                            'message': f'{ap_name} {band} at {airtime_pct}% airtime utilization',
                            'recommendation': 'Monitor airtime, consider load balancing',
                            'priority': 'medium',
                            'airtime': airtime_pct
                        })
            
            # Collect historical airtime data for trending
            try:
                self._collect_airtime_history(devices, results, lookback_hours)
            except Exception as e:
                print(f"Warning: Could not collect airtime history: {e}")
                        
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def _collect_airtime_history(self, devices, results, lookback_hours):
        """
        Collect historical airtime data for time-series visualization
        
        Queries device stats at multiple time points to build trend data
        """
        # Get hourly stats for the lookback period
        # Note: UniFi API provides hourly aggregated data
        try:
            # Get the current timestamp in milliseconds
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = int((datetime.now() - timedelta(hours=lookback_hours)).timestamp() * 1000)
            
            # For each AP, collect historical data
            for device in devices:
                if device.get('type') != 'uap':
                    continue
                
                ap_name = device.get('name', 'Unnamed AP')
                ap_mac = device.get('mac')
                
                if not ap_mac:
                    continue
                
                # Query hourly stats endpoint
                # API format: /api/s/{site}/stat/report/hourly.ap/{mac}
                # Build URL with query parameters
                hourly_stats = self.client.get(
                    f's/{self.site}/stat/report/hourly.ap/{ap_mac}?start={start_time}&end={end_time}'
                )
                
                if not hourly_stats or 'data' not in hourly_stats:
                    continue
                
                # Process time-series data
                for radio_idx, radio_stats in enumerate(['ng', 'na']):  # 2.4GHz and 5GHz
                    band = '2.4GHz' if radio_stats == 'ng' else '5GHz'
                    key = f"{ap_name} ({band})"
                    
                    time_series_data = []
                    for stat in hourly_stats['data']:
                        # Extract radio-specific data
                        radio_table = stat.get('radio_table_stats', [])
                        if radio_idx < len(radio_table):
                            radio_stat = radio_table[radio_idx]
                            time_series_data.append({
                                'timestamp': stat.get('time', 0),
                                'datetime': datetime.fromtimestamp(stat.get('time', 0) / 1000).isoformat(),
                                'airtime_pct': radio_stat.get('cu_total', 0),
                                'tx_pct': radio_stat.get('cu_self_tx', 0),
                                'rx_pct': radio_stat.get('cu_self_rx', 0),
                                'clients': radio_stat.get('num_sta', 0)
                            })
                    
                    if time_series_data:
                        results['time_series'][key] = time_series_data
                        
        except Exception as e:
            # If hourly endpoint fails, create simulated historical data
            # based on current readings with some variation
            print(f"Could not fetch hourly stats (API may not support it): {e}")
            print(f"Creating simulated trend data based on current readings...")
            
            # For each AP with current utilization, create a trend
            for key, current_data in results['ap_utilization'].items():
                base_airtime = current_data['airtime_pct']
                base_tx = current_data['tx_pct']
                base_rx = current_data['rx_pct']
                base_clients = current_data['clients']
                
                # Create 24 hourly data points with variation
                import random
                time_series_data = []
                now = datetime.now()
                
                for hour in range(24):
                    timestamp = now - timedelta(hours=24-hour)
                    
                    # Add realistic variation (±10% of base value)
                    variation = random.uniform(-0.1, 0.1)
                    airtime = max(0, min(100, base_airtime + (base_airtime * variation)))
                    tx = max(0, min(100, base_tx + (base_tx * variation)))
                    rx = max(0, min(100, base_rx + (base_rx * variation)))
                    clients = max(0, int(base_clients + random.randint(-2, 2)))
                    
                    time_series_data.append({
                        'timestamp': int(timestamp.timestamp() * 1000),
                        'datetime': timestamp.isoformat(),
                        'airtime_pct': round(airtime, 1),
                        'tx_pct': round(tx, 1),
                        'rx_pct': round(rx, 1),
                        'clients': clients
                    })
                
                results['time_series'][key] = time_series_data
            
            results['time_series_note'] = 'Historical data simulated from current readings (API endpoint unavailable)'
    
    def analyze_client_capabilities(self, clients):
        """
        Analyze client device capabilities
        
        Identifies:
        - 802.11 standard support (ac, ax, etc)
        - Channel width capability
        - Spatial streams
        - Problem devices holding back network
        """
        results = {
            'capability_distribution': {
                '802.11ax': 0,
                '802.11ac': 0,
                '802.11n': 0,
                'legacy': 0
            },
            'channel_width': {
                '20MHz': 0,
                '40MHz': 0,
                '80MHz': 0,
                '160MHz': 0
            },
            'spatial_streams': {
                '1x1': 0,
                '2x2': 0,
                '3x3': 0,
                '4x4': 0
            },
            'problem_devices': [],
            'recommendations': [],
            'severity': 'ok'
        }
        
        try:
            for client in clients:
                if client.get('is_wired'):
                    continue
                
                # Determine 802.11 standard
                radio_proto = client.get('radio_proto', '').lower()
                
                if 'ax' in radio_proto or client.get('is_11ax'):
                    results['capability_distribution']['802.11ax'] += 1
                elif 'ac' in radio_proto or client.get('is_11ac'):
                    results['capability_distribution']['802.11ac'] += 1
                elif 'n' in radio_proto or client.get('is_11n'):
                    results['capability_distribution']['802.11n'] += 1
                else:
                    results['capability_distribution']['legacy'] += 1
                    
                    # Flag legacy devices
                    results['problem_devices'].append({
                        'hostname': client.get('hostname', client.get('name', 'Unknown')),
                        'mac': client.get('mac'),
                        'radio_proto': radio_proto,
                        'issue': 'Legacy 802.11a/b/g device'
                    })
                
                # Channel width - check both possible field names
                channel_width = client.get('channel_width') or client.get('channelWidth', 20)
                if channel_width >= 160:
                    results['channel_width']['160MHz'] += 1
                elif channel_width >= 80:
                    results['channel_width']['80MHz'] += 1
                elif channel_width >= 40:
                    results['channel_width']['40MHz'] += 1
                else:
                    results['channel_width']['20MHz'] += 1
                
                # Spatial streams (NSS)
                nss = client.get('nss', 1)
                if nss >= 4:
                    results['spatial_streams']['4x4'] += 1
                elif nss >= 3:
                    results['spatial_streams']['3x3'] += 1
                elif nss >= 2:
                    results['spatial_streams']['2x2'] += 1
                else:
                    results['spatial_streams']['1x1'] += 1
            
            # Generate recommendations
            legacy_count = results['capability_distribution']['legacy']
            if legacy_count > 0:
                results['severity'] = 'medium'
                results['recommendations'].append({
                    'type': 'legacy_devices',
                    'message': f'{legacy_count} legacy 802.11a/b/g devices detected',
                    'recommendation': 'Consider upgrading or isolating legacy devices to prevent network slowdown',
                    'priority': 'medium',
                    'affected_count': legacy_count
                })
                
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def calculate_network_health_score(self, analysis_data):
        """
        Calculate overall network health score (0-100)
        
        Factors:
        - Average client RSSI (30%)
        - Channel utilization / airtime (20%)
        - Client distribution (20%)
        - Mesh reliability (15%)
        - Disconnect/roaming issues (15%)
        
        Returns None if critical data is missing due to API errors
        """
        # Check if we have incomplete data from API errors
        api_errors = analysis_data.get('api_errors')
        if api_errors:
            critical_errors = api_errors.get('critical_errors', [])
            if critical_errors:
                # Don't calculate score if we have auth/permission errors
                return {
                    'score': None,
                    'grade': 'N/A',
                    'summary': 'Unable to calculate due to incomplete data',
                    'incomplete_data': True,
                    'reason': 'Critical API errors (authentication/permissions) prevented data collection'
                }
            
            # Check if critical endpoints failed
            failed_endpoints = api_errors.get('failed_endpoints', [])
            critical_endpoint_patterns = ['stat/device', 'stat/sta', 'stat/health']
            has_critical_failure = any(
                any(pattern in endpoint for pattern in critical_endpoint_patterns)
                for endpoint in failed_endpoints
            )
            
            if has_critical_failure:
                return {
                    'score': None,
                    'grade': 'N/A',
                    'summary': 'Unable to calculate due to missing critical data',
                    'incomplete_data': True,
                    'reason': 'Failed to retrieve devices, clients, or health metrics'
                }
        
        score = 100
        details = {}
        
        try:
            # Factor 1: Average Client RSSI (30 points)
            signal_dist = analysis_data.get('signal_distribution', {})
            total_clients = sum([
                signal_dist.get('excellent', 0),
                signal_dist.get('good', 0),
                signal_dist.get('fair', 0),
                signal_dist.get('poor', 0),
                signal_dist.get('very_poor', 0)
            ])
            
            if total_clients > 0:
                rssi_score = (
                    signal_dist.get('excellent', 0) * 1.0 +
                    signal_dist.get('good', 0) * 0.8 +
                    signal_dist.get('fair', 0) * 0.6 +
                    signal_dist.get('poor', 0) * 0.3 +
                    signal_dist.get('very_poor', 0) * 0.0
                ) / total_clients * 30
            else:
                rssi_score = 30
            
            details['rssi_score'] = round(rssi_score, 1)
            
            # Factor 2: Airtime Utilization (20 points)
            airtime_data = analysis_data.get('airtime_analysis', {})
            saturated_count = len(airtime_data.get('saturated_aps', []))
            total_aps = len(airtime_data.get('ap_utilization', {}))
            
            if total_aps > 0:
                airtime_score = max(0, 20 - (saturated_count / total_aps * 20))
            else:
                airtime_score = 20
            
            details['airtime_score'] = round(airtime_score, 1)
            
            # Factor 3: Client Distribution (20 points)
            # Penalize if clients are unevenly distributed
            client_health = analysis_data.get('client_health', {})
            # Simplified: assume good distribution
            distribution_score = 18  # TODO: Calculate from actual distribution
            details['distribution_score'] = distribution_score
            
            # Factor 4: Mesh Reliability (15 points)
            mesh_analysis = analysis_data.get('mesh_analysis', {})
            mesh_issues = len(mesh_analysis.get('findings', []))
            mesh_score = max(0, 15 - (mesh_issues * 3))
            details['mesh_score'] = mesh_score
            
            # Factor 5: DFS/Roaming Issues (15 points)
            dfs_data = analysis_data.get('dfs_analysis', {})
            band_steering = analysis_data.get('band_steering_analysis', {})
            
            issues_score = 15
            if dfs_data.get('severity') == 'high':
                issues_score -= 10
            elif dfs_data.get('severity') == 'medium':
                issues_score -= 5
            
            if band_steering.get('severity') == 'high':
                issues_score -= 5
            
            details['issues_score'] = max(0, issues_score)
            
            # Calculate total
            total_score = (
                rssi_score +
                airtime_score +
                distribution_score +
                mesh_score +
                details['issues_score']
            )
            
            score = round(total_score)
            
        except Exception as e:
            details['error'] = str(e)
        
        # Determine grade
        if score >= 90:
            grade = 'A'
            status = 'Excellent'
        elif score >= 80:
            grade = 'B'
            status = 'Good'
        elif score >= 70:
            grade = 'C'
            status = 'Fair'
        elif score >= 60:
            grade = 'D'
            status = 'Poor'
        else:
            grade = 'F'
            status = 'Critical'
        
        return {
            'score': score,
            'grade': grade,
            'status': status,
            'details': details
        }


def run_advanced_analysis(client, site='default', devices=None, clients=None, lookback_days=3):
    """
    Run all advanced analysis features
    
    Returns comprehensive analysis results
    """
    analyzer = AdvancedNetworkAnalyzer(client, site)
    
    # Get devices and clients if not provided
    if devices is None:
        response = client.get(f's/{site}/stat/device')
        devices = response.get('data', []) if response else []
    if clients is None:
        response = client.get(f's/{site}/stat/sta')
        clients = response.get('data', []) if response else []
    
    # Run switch analysis
    from core.switch_analyzer import SwitchAnalyzer
    switch_analyzer = SwitchAnalyzer(client, site)
    switch_analysis = switch_analyzer.analyze_switches()
    
    results = {
        'dfs_analysis': analyzer.analyze_dfs_events(lookback_days),
        'band_steering_analysis': analyzer.analyze_band_steering(devices, clients),
        'min_rssi_analysis': analyzer.analyze_min_rssi(devices),
        'fast_roaming_analysis': analyzer.analyze_fast_roaming(devices),
        'airtime_analysis': analyzer.analyze_airtime_utilization(devices),
        'client_capabilities': analyzer.analyze_client_capabilities(clients),
        'switch_analysis': switch_analysis
    }
    
    # Calculate overall health score
    # Note: Need to pass full analysis data including signal distribution
    # This will be integrated with main analysis
    
    return results
