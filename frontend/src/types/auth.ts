export interface SessionInfo {
  authenticated: boolean;
  username: string;
  source?: string;
  password_change_required?: boolean;
}

export interface BootstrapStatus {
  bootstrap_required: boolean;
  bootstrap_expired: boolean;
}

export interface PasswordChangedResponse {
  password_changed: true;
  authenticated: false;
}

export type AuthErrorCode =
  | 'password_change_required'
  | 'current_password_required'
  | 'current_password_not_allowed'
  | 'current_password_incorrect'
  | 'new_password_invalid'
  | 'new_password_unchanged'
  | 'password_changed_elsewhere'
  | 'password_change_rate_limited'
  | 'bootstrap_expired';
