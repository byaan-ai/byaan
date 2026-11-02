import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Loader2, ChevronDown, ChevronRight } from 'lucide-react';
import type { ApiAuthType, CustomSkill, CreateCustomSkillData } from '@/stores/slices/contextSlice';

interface WriteSkillModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (data: CreateCustomSkillData) => Promise<void>;
  editingSkill?: CustomSkill | null;
  readOnly?: boolean;
}

export function WriteSkillModal({ isOpen, onClose, onSave, editingSkill, readOnly }: WriteSkillModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [apiEnabled, setApiEnabled] = useState(false);
  const [apiBaseUrl, setApiBaseUrl] = useState('');
  const [apiType, setApiType] = useState<'rest' | 'graphql'>('rest');
  const [apiAuthType, setApiAuthType] = useState<ApiAuthType>('bearer');
  const [apiAuthHeader, setApiAuthHeader] = useState('');
  const [apiDomain, setApiDomain] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSectionOpen, setApiSectionOpen] = useState(false);

  useEffect(() => {
    if (editingSkill) {
      setName(editingSkill.name ?? '');
      setDescription(editingSkill.description ?? '');
      setInstructions(editingSkill.instructions ?? '');
      const hasApi = !!(editingSkill.api_base_url && editingSkill.has_credentials);
      setApiEnabled(hasApi);
      setApiBaseUrl(editingSkill.api_base_url ?? '');
      setApiType((editingSkill.api_type as 'rest' | 'graphql') ?? 'rest');
      setApiAuthType((editingSkill.api_auth_type as ApiAuthType) ?? 'bearer');
      setApiAuthHeader(editingSkill.api_auth_header ?? '');
      setApiDomain(editingSkill.api_domain ?? '');
      setApiKey('');
      setApiSectionOpen(hasApi);
    } else {
      setName('');
      setDescription('');
      setInstructions('');
      setApiEnabled(false);
      setApiBaseUrl('');
      setApiType('rest');
      setApiAuthType('bearer');
      setApiAuthHeader('');
      setApiDomain('');
      setApiKey('');
      setApiSectionOpen(false);
    }
    setError(null);
  }, [editingSkill, isOpen]);

  const isValid = name?.trim() && description?.trim() && instructions?.trim()
    && (!apiEnabled || (apiBaseUrl?.trim() && (apiAuthType !== 'header' || apiAuthHeader.trim())));

  const isNewApiSetup = apiEnabled && !editingSkill?.has_credentials;
  const apiKeyRequired = isNewApiSetup && !apiKey?.trim();

  async function handleSave() {
    if (!isValid || apiKeyRequired) return;

    setIsSaving(true);
    setError(null);

    try {
      const data: CreateCustomSkillData = {
        name: name.trim(),
        description: description.trim(),
        instructions: instructions.trim(),
      };

      if (apiEnabled && apiBaseUrl.trim()) {
        data.api_config = {
          api_base_url: apiBaseUrl.trim(),
          api_type: apiType,
          api_auth_type: apiAuthType,
          api_auth_header: apiAuthType === 'header' ? apiAuthHeader.trim() : '',
          api_domain: apiDomain.trim(),
          api_key: apiKey,
        };
      } else if (!apiEnabled && editingSkill?.can_execute_api) {
        data.remove_api_config = true;
      }

      await onSave(data);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save skill');
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="bg-[#0d0d0d] border-gray-800 max-w-2xl max-h-[90vh] overflow-y-auto custom-scrollbar">
        <DialogHeader>
          <DialogTitle className="text-white">
            {readOnly ? editingSkill?.name : editingSkill ? 'Edit Custom Skill' : 'Create Custom Skill'}
          </DialogTitle>
        </DialogHeader>

        {readOnly ? (
          <>
            <div className="space-y-4 py-4">
              <div className="space-y-1">
                <label className="text-sm font-medium text-gray-300">Description</label>
                <p className="text-sm text-gray-400">{editingSkill?.description}</p>
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium text-gray-300">Instructions</label>
                <div className="p-3 bg-[#1a1a1a] border border-gray-700 rounded-md text-sm text-gray-300 whitespace-pre-wrap max-h-[50vh] overflow-y-auto custom-scrollbar">
                  {editingSkill?.instructions}
                </div>
              </div>
              {editingSkill?.can_execute_api && (
                <div className="space-y-1">
                  <label className="text-sm font-medium text-gray-300">API Configuration</label>
                  <div className="p-3 bg-[#1a1a1a] border border-gray-700 rounded-md text-sm text-gray-400 space-y-1">
                    <p>Base URL: {editingSkill.api_base_url}</p>
                    <p>Type: {editingSkill.api_type?.toUpperCase()}</p>
                    <p>Auth: {editingSkill.api_auth_type}{editingSkill.api_auth_header ? ` (${editingSkill.api_auth_header})` : ''}</p>
                    {editingSkill.api_domain && <p>Domain: {editingSkill.api_domain}</p>}
                  </div>
                </div>
              )}
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={onClose}>Close</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-300">Skill Name</label>
                <Input
                  placeholder="e.g., weekly-status-report"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="bg-[#1a1a1a] border-gray-700 text-white"
                />
                <p className="text-xs text-gray-500">
                  A short identifier for the skill (used when searching)
                </p>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-300">Description</label>
                <Input
                  placeholder="When to use this skill..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="bg-[#1a1a1a] border-gray-700 text-white"
                />
                <p className="text-xs text-gray-500">
                  Brief description of when the AI should use this skill
                </p>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-300">Instructions</label>
                <textarea
                  placeholder="Write the instructions for this skill...

Example:
When generating a status report, summarize work in three sections:

## Wins
- List completed items

## Blockers
- Any issues

## Next Steps
- Planned work"
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  rows={12}
                  className="w-full px-3 py-2 bg-[#1a1a1a] border border-gray-700 rounded-md text-white text-sm placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-brand-orange resize-y"
                />
                <p className="text-xs text-gray-500">
                  Detailed instructions the AI will follow. Markdown is supported.
                </p>
              </div>

              {/* API Configuration Toggle */}
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <div
                  className="flex items-center justify-between p-3 bg-[#1a1a1a] cursor-pointer"
                  onClick={() => {
                    if (!apiEnabled) {
                      setApiEnabled(true);
                      setApiSectionOpen(true);
                    } else {
                      setApiSectionOpen(!apiSectionOpen);
                    }
                  }}
                >
                  <div className="flex items-center gap-3">
                    {apiEnabled ? (
                      apiSectionOpen ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />
                    ) : (
                      <ChevronRight className="w-4 h-4 text-gray-400" />
                    )}
                    <span className="text-sm font-medium text-gray-300">Enable API Calls</span>
                  </div>
                  <div onClick={(e) => e.stopPropagation()}>
                    <Switch
                      checked={apiEnabled}
                      onCheckedChange={(checked) => {
                        setApiEnabled(checked);
                        setApiSectionOpen(checked);
                      }}
                    />
                  </div>
                </div>

                {apiEnabled && apiSectionOpen && (
                  <div className="p-3 space-y-3 border-t border-gray-700">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-gray-300">Base URL *</label>
                      <Input
                        placeholder="https://api.example.com"
                        value={apiBaseUrl}
                        onChange={(e) => setApiBaseUrl(e.target.value)}
                        className="bg-[#0d0d0d] border-gray-700 text-sm"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-gray-300">API Type</label>
                        <select
                          value={apiType}
                          onChange={(e) => setApiType(e.target.value as 'rest' | 'graphql')}
                          className="w-full px-3 py-2 bg-[#0d0d0d] border border-gray-700 rounded-md text-white text-sm focus:outline-none focus:ring-2 focus:ring-brand-orange"
                        >
                          <option value="rest">REST</option>
                          <option value="graphql">GraphQL</option>
                        </select>
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-gray-300">Auth Type</label>
                        <select
                          value={apiAuthType}
                          onChange={(e) => setApiAuthType(e.target.value as ApiAuthType)}
                          className="w-full px-3 py-2 bg-[#0d0d0d] border border-gray-700 rounded-md text-white text-sm focus:outline-none focus:ring-2 focus:ring-brand-orange"
                        >
                          <option value="bearer">Bearer Token</option>
                          <option value="custom">Raw Authorization Header</option>
                          <option value="header">Custom Header Name</option>
                        </select>
                      </div>
                    </div>

                    {apiAuthType === 'header' && (
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-gray-300">Header Name *</label>
                        <Input
                          placeholder="e.g. xi-api-key"
                          value={apiAuthHeader}
                          onChange={(e) => setApiAuthHeader(e.target.value)}
                          className="bg-[#0d0d0d] border-gray-700 text-sm"
                        />
                        <p className="text-xs text-gray-500">The API key is sent as the value of this header.</p>
                      </div>
                    )}

                    <div className="space-y-1">
                      <label className="text-xs font-medium text-gray-300">Domain</label>
                      <Input
                        placeholder="Auto-derived from Base URL if empty"
                        value={apiDomain}
                        onChange={(e) => setApiDomain(e.target.value)}
                        className="bg-[#0d0d0d] border-gray-700 text-sm"
                      />
                    </div>

                    <div className="space-y-1">
                      <label className="text-xs font-medium text-gray-300">
                        API Key {isNewApiSetup ? '*' : ''}
                      </label>
                      <Input
                        type="password"
                        placeholder={editingSkill?.has_credentials ? 'Leave blank to keep current' : 'Enter API key'}
                        value={apiKey}
                        onChange={(e) => setApiKey(e.target.value)}
                        className="bg-[#0d0d0d] border-gray-700 text-sm"
                      />
                    </div>
                  </div>
                )}
              </div>

              {error && (
                <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-md">
                  <p className="text-sm text-red-400">{error}</p>
                </div>
              )}

              {!apiEnabled && (
                <div className="p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-md">
                  <p className="text-xs text-yellow-400">
                    Custom skills are informational only. The AI will follow these instructions
                    but cannot make external API calls for custom skills.
                  </p>
                </div>
              )}
            </div>

            <DialogFooter>
              <Button
                variant="ghost"
                onClick={onClose}
                disabled={isSaving}
              >
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                disabled={!isValid || apiKeyRequired || isSaving}
                className="bg-brand-orange hover:bg-brand-orange/90"
              >
                {isSaving ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Saving...
                  </>
                ) : (
                  editingSkill ? 'Save Changes' : 'Create Skill'
                )}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
