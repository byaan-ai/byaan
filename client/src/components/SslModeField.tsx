import { Lock } from 'lucide-react'
import { Label } from './ui/label'
import { Switch } from './ui/switch'

export type SslMode = 'require' | 'verify-full'

const SSL_MODE_OPTIONS: { value: SslMode; label: string; description: string }[] = [
  {
    value: 'require',
    label: 'Encrypt',
    description: 'Encrypts traffic without checking the certificate. Use with SSH/SSM tunnels or self-signed certs.',
  },
  {
    value: 'verify-full',
    label: 'Encrypt + verify',
    description: 'Also validates the server certificate and hostname against trusted CAs.',
  },
]

interface SslModeFieldProps {
  enabled: boolean
  mode: SslMode
  onEnabledChange: (enabled: boolean) => void
  onModeChange: (mode: SslMode) => void
  disabled?: boolean
}

export function SslModeField({ enabled, mode, onEnabledChange, onModeChange, disabled = false }: SslModeFieldProps) {
  return (
    <div className="rounded-md border border-[#555555] bg-[#1a1a1a] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <Lock className={`mt-0.5 h-4 w-4 flex-shrink-0 ${enabled ? 'text-brand-orange' : 'text-gray-500'}`} />
          <div>
            <Label htmlFor="ssl-enabled" className="text-white cursor-pointer">Use SSL/TLS</Label>
            <p className="mt-0.5 text-xs text-gray-400">
              Required by most managed databases (AWS RDS, Azure, Supabase, Neon).
            </p>
          </div>
        </div>
        <Switch id="ssl-enabled" checked={enabled} onCheckedChange={onEnabledChange} disabled={disabled} />
      </div>

      {enabled && (
        <div role="radiogroup" aria-label="SSL mode" className="mt-3 grid grid-cols-2 gap-2">
          {SSL_MODE_OPTIONS.map(option => {
            const selected = mode === option.value
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => onModeChange(option.value)}
                disabled={disabled}
                className={`rounded-md border p-2.5 text-left transition-colors disabled:opacity-50 ${
                  selected
                    ? 'border-brand-orange bg-brand-orange/10'
                    : 'border-[#444444] hover:border-[#666666] hover:bg-[#2a2a2a]'
                }`}
              >
                <div className="text-sm font-medium text-white">{option.label}</div>
                <div className="mt-0.5 text-xs leading-snug text-gray-400">{option.description}</div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
