import { NextRequest, NextResponse } from 'next/server';
import {
  agristackProxyEnabled,
  agristackLambdaWebhookUrls,
  callAgristackLambda,
} from '../../lib/agristackLambdaProxy';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const customHeadersStr = request.headers.get('x-custom-headers');
    let customHeaders: Record<string, string> = {};
    if (customHeadersStr) {
      try {
        customHeaders = JSON.parse(customHeadersStr);
      } catch {
        console.error('Failed to parse custom headers');
      }
    }

    // Mumbai Lambda path — preserves full Seek JSON from the UI
    if (agristackProxyEnabled()) {
      const auth =
        customHeaders.Authorization ||
        customHeaders.authorization ||
        '';
      const token = auth.toLowerCase().startsWith('bearer ')
        ? auth.slice(7).trim()
        : auth.trim();

      const hooks = agristackLambdaWebhookUrls();
      const seekBody = body && typeof body === 'object' ? { ...body } : body;
      if (
        seekBody?.header &&
        typeof seekBody.header === 'object' &&
        hooks?.farmers
      ) {
        const cur = String(seekBody.header.sender_uri || '');
        if (!cur || /localhost|127\.0\.0\.1/i.test(cur)) {
          seekBody.header = {
            ...seekBody.header,
            sender_uri: hooks.farmers,
          };
        }
      }

      const { json, responseTime, status } = await callAgristackLambda('seek', {
        access_token: token,
        seek_body: seekBody,
      });

      // Normalize to existing sandbox UI shape
      return NextResponse.json(
        {
          success: Boolean(json.success ?? json.ok ?? status < 400),
          data: json.data ?? json,
          statusCode: Number(json.statusCode ?? json.status ?? status),
          responseTime: Number(json.responseTime ?? json.ms ?? responseTime),
          via: 'lambda-ap-south-1',
        },
        { status: status >= 400 && status !== 401 ? status : 200 }
      );
    }

    const startTime = Date.now();
    const response = await fetch(
      'https://sandbox.agristack.gov.in/sandbox-api/agristack/seek',
      {
        method: 'POST',
        headers: {
          ...customHeaders,
        },
        body: JSON.stringify(body),
      }
    );

    const responseTime = Date.now() - startTime;
    const responseText = await response.text();
    let data;
    try {
      data = JSON.parse(responseText);
    } catch {
      data = responseText;
    }

    return NextResponse.json({
      success: response.ok,
      data: data,
      statusCode: response.status,
      responseTime,
    });
  } catch (error) {
    console.error('API Error:', error);
    return NextResponse.json(
      {
        success: false,
        error: {
          code: 500,
          message: error instanceof Error ? error.message : 'Internal server error',
        },
        statusCode: 500,
      },
      { status: 500 }
    );
  }
}
