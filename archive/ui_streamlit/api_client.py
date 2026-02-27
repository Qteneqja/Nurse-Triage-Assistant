"""API Client for Triage Backend"""
import requests
from typing import Optional, Dict, Any
import streamlit as st

class TriageAPIClient:
    """Client to interact with the FastAPI triage backend"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8001"):
        self.base_url = base_url.rstrip("/")
        self.timeout = 30
    
    def _handle_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[Any, Any]]:
        """Make HTTP request with error handling"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.Timeout:
            st.error("Request timeout. Please check if the backend server is running.")
            return None
        
        except requests.exceptions.ConnectionError:
            st.error(f"Cannot connect to backend at {self.base_url}. Is the server running?")
            return None
        
        except requests.exceptions.HTTPError as e:
            st.error(f"Server error: {e.response.status_code} - {e.response.text}")
            return None
        
        except Exception as e:
            st.error(f"Unexpected error: {str(e)}")
            return None
    
    def health_check(self) -> bool:
        """Check if the backend is healthy"""
        result = self._handle_request("GET", "/health")
        return result is not None
    
    def start_session(self) -> Optional[Dict[str, Any]]:
        """Start a new triage session"""
        return self._handle_request("POST", "/api/v1/intake/start", json={})
    
    def send_answer(self, session_id: str, answer: str) -> Optional[Dict[str, Any]]:
        """Send answer to current question"""
        return self._handle_request(
            "POST",
            f"/api/v1/intake/{session_id}/answer",
            json={"answer": answer}
        )
    
    def get_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get intake summary"""
        return self._handle_request("GET", f"/api/v1/intake/{session_id}/summary")
    
    def finalize_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get final triage decision"""
        return self._handle_request("POST", f"/api/v1/intake/{session_id}/finalize", json={})
