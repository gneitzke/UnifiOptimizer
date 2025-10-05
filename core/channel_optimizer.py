#!/usr/bin/env python3
"""
Smart Channel Recommendation System

Tracks channel recommendations and only suggests changes when:
1. There's a genuine problem (overlap, interference)
2. The recommendation hasn't been made recently
3. The current channel is actually problematic

Prevents repeated recommendations for channels that were already changed.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path


class ChannelRecommendationTracker:
    """Track channel recommendations to avoid repeated suggestions"""

    def __init__(self, cache_file=".channel_recommendations.json"):
        self.cache_file = Path(cache_file)
        self.recommendations_history = self._load_history()

    def _load_history(self):
        """Load recommendation history from cache"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_history(self):
        """Save recommendation history to cache"""
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.recommendations_history, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save channel recommendation history: {e}")

    def should_recommend(self, ap_mac, band, current_channel, proposed_channel, reason):
        """
        Determine if we should recommend a channel change

        Args:
            ap_mac: MAC address of the AP
            band: '2.4GHz' or '5GHz'
            current_channel: Current channel number
            proposed_channel: Proposed new channel
            reason: Reason for the change

        Returns:
            tuple: (should_recommend: bool, message: str)
        """
        key = f"{ap_mac}_{band}"
        now = datetime.now().isoformat()

        # Check if we've recommended this before
        if key in self.recommendations_history:
            history = self.recommendations_history[key]
            last_recommended = history.get("last_recommended_channel")
            last_date = history.get("last_date")

            # If we recommended this exact channel in the last 30 days, skip
            if last_recommended == proposed_channel and last_date:
                try:
                    last_datetime = datetime.fromisoformat(last_date)
                    if datetime.now() - last_datetime < timedelta(days=30):
                        return (
                            False,
                            f"Already recommended channel {proposed_channel} on {last_date[:10]}",
                        )
                except Exception:
                    pass

            # If current channel matches what we last recommended, and it's recent, skip
            if current_channel == last_recommended and last_date:
                try:
                    last_datetime = datetime.fromisoformat(last_date)
                    if datetime.now() - last_datetime < timedelta(days=7):
                        return False, f"Already changed to recommended channel {current_channel}"
                except Exception:
                    pass

        # Record this recommendation
        self.recommendations_history[key] = {
            "ap_mac": ap_mac,
            "band": band,
            "current_channel": current_channel,
            "last_recommended_channel": proposed_channel,
            "last_date": now,
            "reason": reason,
        }
        self._save_history()

        return True, f"New recommendation: {current_channel} → {proposed_channel}"

    def clear_old_recommendations(self, days=90):
        """Clear recommendations older than specified days"""
        cutoff = datetime.now() - timedelta(days=days)
        keys_to_remove = []

        for key, history in self.recommendations_history.items():
            last_date = history.get("last_date")
            if last_date:
                try:
                    last_datetime = datetime.fromisoformat(last_date)
                    if last_datetime < cutoff:
                        keys_to_remove.append(key)
                except Exception:
                    pass

        for key in keys_to_remove:
            del self.recommendations_history[key]

        if keys_to_remove:
            self._save_history()


def analyze_channels_smart(ap_analysis, tracker=None):
    """
    Smart channel analysis that avoids repeated recommendations

    Args:
        ap_analysis: AP analysis results
        tracker: ChannelRecommendationTracker instance (optional)

    Returns:
        list: Channel recommendations (filtered to avoid repeats)
    """
    if tracker is None:
        tracker = ChannelRecommendationTracker()

    recommendations = []
    CHANNEL_24_PREFERRED = [1, 6, 11]

    # Analyze 2.4GHz channels
    # NOTE: With multiple APs (e.g., 7 APs), having 2-3 APs per channel is NORMAL and EXPECTED
    # Only flag severe imbalances (e.g., 5 APs on one channel, 1 on another)
    from collections import defaultdict

    channels_24 = defaultdict(list)

    for ap_info in ap_analysis.get("ap_details", []):
        if "2.4GHz" in ap_info["radios"]:
            radio = ap_info["radios"]["2.4GHz"]
            channel = radio["channel"]
            channels_24[channel].append({"ap_info": ap_info, "radio": radio})

    # Check for non-standard channels (definite problem)
    for channel, aps in channels_24.items():
        if channel not in CHANNEL_24_PREFERRED:
            # Find best alternative channel
            channel_loads = {ch: len(channels_24.get(ch, [])) for ch in CHANNEL_24_PREFERRED}
            best_channel = min(channel_loads.keys(), key=lambda ch: channel_loads[ch])

            for ap_data in aps:
                ap_info = ap_data["ap_info"]
                ap_mac = ap_info["mac"]

                should_rec, message = tracker.should_recommend(
                    ap_mac,
                    "2.4GHz",
                    channel,
                    best_channel,
                    f"Non-overlapping channel (currently on {channel})",
                )

                if should_rec:
                    recommendations.append(
                        {
                            "ap": ap_info,
                            "radio": ap_data["radio"],
                            "band": "2.4GHz",
                            "priority": "high",
                            "issue": "non_standard_channel",
                            "current_channel": channel,
                            "new_channel": best_channel,
                            "message": f"Using overlapping channel {channel}",
                            "recommendation": f"Change to non-overlapping channel {best_channel}",
                            "type": "channel_optimization",
                            "reason": message,
                        }
                    )

    # Check for severe co-channel interference (only flag if VERY unbalanced)
    # With 7 APs and 3 channels, expect ~2-3 APs per channel - this is NORMAL
    # Only flag if one channel has significantly more than the others
    for channel, aps in channels_24.items():
        if len(aps) >= 3 and channel in CHANNEL_24_PREFERRED:
            # Calculate if this channel is significantly overloaded vs others
            channel_loads = {ch: len(channels_24.get(ch, [])) for ch in CHANNEL_24_PREFERRED}
            avg_load = sum(channel_loads.values()) / len(channel_loads)

            # Only recommend if this channel has 2+ more APs than average
            # This prevents oscillation when distribution is relatively balanced
            if len(aps) >= avg_load + 2:
                # Find alternative channel with fewer APs
                best_channel = min(
                    [ch for ch in CHANNEL_24_PREFERRED if ch != channel],
                    key=lambda ch: channel_loads[ch],
                )

                # Only recommend moving the AP with fewest clients
                ap_with_fewest = min(aps, key=lambda x: x["ap_info"]["client_count"])
                ap_info = ap_with_fewest["ap_info"]
                ap_mac = ap_info["mac"]

                should_rec, message = tracker.should_recommend(
                    ap_mac,
                    "2.4GHz",
                    channel,
                    best_channel,
                    f"Channel imbalance: {len(aps)} APs on channel {channel} vs avg {avg_load:.1f}",
                )

                if should_rec:
                    recommendations.append(
                        {
                            "ap": ap_info,
                            "radio": ap_with_fewest["radio"],
                            "band": "2.4GHz",
                            "priority": "low",  # Changed from medium - this is less urgent
                            "issue": "channel_imbalance",
                            "current_channel": channel,
                            "new_channel": best_channel,
                            "message": f"Channel {channel} overloaded ({len(aps)} APs vs avg {avg_load:.1f})",
                            "recommendation": f"Move to channel {best_channel} ({channel_loads[best_channel]} APs) for better balance",
                            "type": "channel_optimization",
                            "reason": message,
                        }
                    )

    return recommendations
