"""Fixed diagnostic vocabulary. Never forward response/native text or identifiers."""
OPERATIONS = {'config', 'inventory', 'session', 'report', 'claim', 'credentials',
              'heartbeat', 'complete', 'fail', 'prepare', 'preparation-missing'}
CRITICAL_API_REASONS = {'ACCOUNT_IDENTITY_MISMATCH', 'RESULT_CONFLICT'}
API_REASONS = {
    'SESSION_CHANGED': 'The terminal generation changed; refresh the session before retrying.',
    'SESSION_CONFLICT': 'The requested session conflicts with the registered process.',
    'SLOT_BUSY': 'Another job holds this slot; wait for it to finish.',
    'SLOT_NOT_READY': 'The slot lacks current verified readiness; refresh inventory.',
    'TERMINAL_RESTART_REQUIRED': 'The API quarantined this process; a verified replacement is required.',
    'LEASE_LOST': 'The job lease expired or was replaced; this result cannot be accepted.',
    'CLAIM_CONFLICT': 'The claim identifier was reused with different claim data.',
    'CLAIM_FINISHED': 'This claim has already finished; start a new claim.',
    'CLAIM_CLOCK_OR_AGE': 'The claim timestamp is stale or outside the permitted clock skew.',
    'RESULT_CLOCK_OR_AGE': 'The result timestamp is stale or outside the permitted clock skew.',
    'REPORT_CLOCK_OR_AGE': 'The inventory timestamp is stale or outside the permitted clock skew.',
    'REPORT_OUT_OF_ORDER': 'The inventory sequence is not newer than the accepted report.',
    'RESULT_CONFLICT': 'A completed job received a different result for the same completion identity.',
    'CREDENTIALS_REVOKED': 'Connection authorization or credential version changed; the lease is no longer valid.',
    'TRADING_PASSWORD_CONSENT_REQUIRED': 'The current credentials require owner consent for trading-enabled access.',
    'ACCOUNT_IDENTITY_MISMATCH': 'Collected account identity differs from the authorized connection.',
    'HISTORY_CURSOR_CHANGED': 'The history cursor changed while this job was running.',
    'HISTORY_CURSOR_REQUIRED': 'The collection result is missing its required history cursor.',
    'NODE_SLOT_LIMIT': 'The node exceeds the supported slot limit.',
    'CATALOGUE_MATCH_LIMIT': 'The matching server catalogue exceeds the supported limit.',
    'INVENTORY_LIMIT': 'The inventory exceeds the supported slot or server-state limit.',
}
EXPLANATIONS = {
    'API_TERMINAL_BUILD_UNSUPPORTED': 'This MT5 build passed local review but the API does not accept it yet. Deploy API compatibility before enabling this slot.',
    'TERMINAL_BUILD_UNSUPPORTED': 'MT5 updated to an unverified build. Only this slot is blocked until compatibility is reviewed.',
    'UPDATE_REQUIRES_ATTENTION': 'MT5 updater has shown a persistent dialog for at least ten minutes. Automatic terminal relaunch is blocked.',
    'UPDATE_PROCESS_UNVERIFIED': 'An unfinished MT5 updater remains but its process cannot be verified. Relaunch is blocked to avoid an update loop.',
    'RECOVERY_BUDGET_UNAVAILABLE': 'The shared restart budget cannot be read safely. Recovery is deferred; existing terminals keep running.',
    'API_HTTP_409': 'TradeTrack API rejected a conflicting state. This alone does not indicate an MT5 password failure.',
    'API_HTTP_401': 'TradeTrack API rejected worker authentication; check the node token, not the MT5 password.',
    'API_HTTP_403': 'TradeTrack API denied this worker operation; check node permissions and configuration.',
    'TERMINAL_CHANGED': 'The verified terminal process or catalogue changed; the operation stopped to avoid using stale evidence.',
    'UI_DIALOG_UNAVAILABLE': 'The expected MT5 login dialog did not become available within the bounded wait.',
    'UI_CLEANUP_FAILED': 'The worker could not safely close or restore the MT5 dialog after inspection.',
    'TERMINAL_UI_BUSY': 'Another visible MT5 dialog prevented safe terminal maintenance.',
    'TERMINAL_STOP_TIMEOUT': 'The exact terminal process did not exit after a graceful close request.',
    'AUTH_FAILED': 'MT5 reported authentication failure; check the exact server, credentials and broker account status.',
    'NETWORK_UNAVAILABLE': 'The MT5 connection failed with recognized transport or service-unavailable evidence.',
    'CONNECTION_FAILED': 'MT5 connection failed without enough evidence to distinguish credentials, broker restrictions or transport.',
    'IDENTITY_DRIFT': 'The terminal account identity did not match the requested account; collection was stopped.',
    'INVESTOR_UNVERIFIED': 'Read-only investor access was not positively verified; collection was blocked.',
    'TRADING_ENABLED': 'The terminal reported trading-enabled access; owner confirmation is required.',
    'API_UNAVAILABLE': 'The worker could not reach TradeTrack API after bounded retries.',
    'API_RESPONSE_LIMIT': 'The API response exceeded the bounded client size limit.',
    'CHILD_TIMEOUT': 'The isolated MT5 collection process exceeded its deadline.',
    'WORKER_FAILED': 'An unexpected worker operation failed; inspect the corresponding local worker log.',
}


def api_fields(operation=None, reason=None):
    fields = {}
    if isinstance(operation, str) and operation in OPERATIONS:
        fields['operation'] = operation
    if isinstance(reason, str) and reason in API_REASONS:
        fields['apiReason'] = reason
    return fields


def error_fields(error):
    return api_fields(getattr(error, 'operation', None), getattr(error, 'reason', None))


def explanation(code, reason=None):
    return API_REASONS.get(reason) or EXPLANATIONS.get(code, 'Worker reported this diagnostic code; inspect the matching local log and operation.')
