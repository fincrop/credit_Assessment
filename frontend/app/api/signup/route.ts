import { NextRequest, NextResponse } from 'next/server';
import bcrypt from 'bcryptjs';
import { connectToDatabase } from '../../lib/mongodb';
import { signJWT } from '../../lib/jwt';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const email =
      typeof body.email === 'string'
        ? body.email.trim().toLowerCase().slice(0, 254)
        : '';
    const password =
      typeof body.password === 'string' ? body.password.slice(0, 128) : '';
    const name =
      typeof body.name === 'string' ? body.name.trim().slice(0, 120) : '';

    if (!email || !password || !name) {
      return NextResponse.json(
        { error: 'Name, email, and password are required' },
        { status: 400 }
      );
    }

    if (!EMAIL_RE.test(email)) {
      return NextResponse.json({ error: 'Invalid email address' }, { status: 400 });
    }

    if (password.length < 6) {
      return NextResponse.json(
        { error: 'Password must be at least 6 characters' },
        { status: 400 }
      );
    }

    if (name.length < 2) {
      return NextResponse.json(
        { error: 'Name must be at least 2 characters' },
        { status: 400 }
      );
    }

    const { db } = await connectToDatabase();
    const users = db.collection('users');

    const existing = await users.findOne({ email });
    if (existing) {
      return NextResponse.json(
        { error: 'An account with this email already exists' },
        { status: 409 }
      );
    }

    const hashed = await bcrypt.hash(password, 12);
    const now = new Date();
    const insert = await users.insertOne({
      email,
      password: hashed,
      name,
      role: 'user',
      createdAt: now,
      updatedAt: now,
    });

    const user = {
      id: insert.insertedId.toString(),
      email,
      name,
      role: 'user',
    };

    const token = await signJWT(user);

    const response = NextResponse.json({ user }, { status: 201 });
    response.cookies.set('auth-token', token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      path: '/',
      maxAge: 60 * 60 * 24 * 7,
    });

    return response;
  } catch (error) {
    console.error('Signup error:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}
