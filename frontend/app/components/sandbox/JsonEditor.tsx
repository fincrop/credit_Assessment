'use client';

import { useState, useEffect, useRef } from 'react';

interface JsonEditorProps {
    value: string;
    onChange: (value: string) => void;
    readOnly?: boolean;
}

// Syntax highlighting function for JSON with inline styles
function highlightJson(json: string): string {
    // Escape HTML first
    const escaped = json
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Apply syntax highlighting with inline styles
    return escaped
        // Strings (will be re-processed for keys)
        .replace(/"([^"\\]*(\\.[^"\\]*)*)"/g, (match, content) => {
            return `"<span style="color: #ce9178">${content}</span>"`;
        })
        // Numbers
        .replace(/\b(-?\d+\.?\d*)\b/g, '<span style="color: #b5cea8">$1</span>')
        // Booleans
        .replace(/\b(true|false)\b/g, '<span style="color: #569cd6">$1</span>')
        // Null
        .replace(/\bnull\b/g, '<span style="color: #569cd6">null</span>')
        // Keys (strings followed by colon) - override the string color for keys
        .replace(/"<span style="color: #ce9178">([^<]+)<\/span>"\s*:/g,
            '"<span style="color: #9cdcfe">$1</span>":')
        // Brackets and braces
        .replace(/([{}\[\]])/g, '<span style="color: #ffd700">$1</span>')
        // Colons after keys
        .replace(/(<\/span>"):/g, '$1<span style="color: #d4d4d4">:</span>')
        // Commas
        .replace(/,/g, '<span style="color: #d4d4d4">,</span>');
}

export default function JsonEditor({ value, onChange, readOnly = false }: JsonEditorProps) {
    const [lines, setLines] = useState<string[]>([]);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const highlightRef = useRef<HTMLPreElement>(null);

    useEffect(() => {
        setLines(value.split('\n'));
    }, [value]);

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        onChange(e.target.value);
    };

    const handleScroll = () => {
        if (textareaRef.current && highlightRef.current) {
            highlightRef.current.scrollTop = textareaRef.current.scrollTop;
            highlightRef.current.scrollLeft = textareaRef.current.scrollLeft;
        }
    };

    return (
        <div className="bg-[#1e1e1e] rounded-lg overflow-hidden font-mono text-sm">
            <div className="flex">
                {/* Line Numbers */}
                <div className="bg-[#252526] text-stone-500 py-4 px-3 text-right select-none min-w-[50px] flex-shrink-0">
                    {lines.map((_, index) => (
                        <div key={index} className="leading-6 h-6">
                            {index + 1}
                        </div>
                    ))}
                </div>

                {/* Code Editor Container */}
                <div className="flex-1 relative min-h-[300px]">
                    {/* Syntax Highlighted Layer (background) */}
                    <pre
                        ref={highlightRef}
                        className="absolute inset-0 p-4 overflow-auto leading-6 pointer-events-none whitespace-pre-wrap break-words m-0"
                        style={{
                            fontFamily: 'Monaco, Menlo, "Ubuntu Mono", Consolas, source-code-pro, monospace',
                            fontSize: '14px',
                            color: '#d4d4d4',
                        }}
                        dangerouslySetInnerHTML={{ __html: highlightJson(value) + '\n' }}
                    />

                    {/* Transparent Textarea Layer (foreground for editing) */}
                    <textarea
                        ref={textareaRef}
                        value={value}
                        onChange={handleChange}
                        onScroll={handleScroll}
                        readOnly={readOnly}
                        className="absolute inset-0 w-full h-full bg-transparent p-4 resize-none focus:outline-none leading-6 whitespace-pre-wrap break-words"
                        spellCheck={false}
                        style={{
                            fontFamily: 'Monaco, Menlo, "Ubuntu Mono", Consolas, source-code-pro, monospace',
                            fontSize: '14px',
                            color: 'transparent',
                            caretColor: 'white',
                        }}
                    />
                </div>
            </div>
        </div>
    );
}
